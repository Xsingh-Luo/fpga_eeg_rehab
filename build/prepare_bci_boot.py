"""Prepare an isolated BCI DT/FIT from the existing 7020 image. Never writes a disk."""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import struct
from inspect_boot_image import fdt_nodes


def encode_fdt(nodes, template):
    """Preserve properties/reservations, emit a standard v17 FDT with 4-byte tokens."""
    string_data = bytearray()
    offsets = {}
    for props in nodes.values():
        for name in props:
            if name not in offsets:
                offsets[name] = len(string_data)
                string_data.extend(name.encode()+b'\0')
    children = {name: [] for name in nodes}
    for name in nodes:
        if name:
            parent = name.rsplit('/', 1)[0]
            children[parent].append(name)

    def padded(value):
        return value + b'\0' * (-len(value) % 4)

    def emit(name):
        result = bytearray(struct.pack('>I', 1))
        result.extend(padded(name.rsplit('/', 1)[-1].encode()+b'\0'))
        for key, value in nodes[name].items():
            result.extend(struct.pack('>III', 3, len(value), offsets[key]))
            result.extend(padded(value))
        for child in children[name]:
            result.extend(emit(child))
        result.extend(struct.pack('>I', 2))
        return result

    body = bytes(emit('')) + struct.pack('>I', 9)
    old_reserve = struct.unpack_from('>I', template, 16)[0]
    end = old_reserve
    while template[end:end+16] != bytes(16):
        end += 16
        if end+16 > len(template):
            raise ValueError('Invalid reservation block')
    reserve = template[old_reserve:end+16]
    start = 40 + len(reserve)
    strings = start + len(body)
    header = struct.pack('>10I', 0xd00dfeed, strings+len(string_data), start, strings,
                         40, 17, 16, struct.unpack_from('>I', template, 28)[0], len(string_data), len(body))
    result = header + reserve + body + string_data
    if fdt_nodes(result) != nodes:
        raise ValueError('FDT serialization did not preserve all nodes/properties')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--factory-fit', type=Path, required=True)
    p.add_argument('--bitstream', type=Path)
    p.add_argument('--implementation-status', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--lcd-emio', action='store_true', help='Only for a verified platform lcd_emio implementation')
    p.add_argument('--knob-emio', action='store_true', help='Requires matching three-input EC11 fabric')
    a = p.parse_args()
    if a.knob_emio and not a.lcd_emio:
        p.error('--knob-emio requires --lcd-emio')
    original = a.factory_fit.read_bytes()
    fit = fdt_nodes(original)
    for node, props in fit.items():
        if 'algo' in props and 'value' in props:
            parent = fit[node.rsplit('/', 1)[0]]
            if hashlib.new(props['algo'].rstrip(b'\0').decode(), parent['data']).digest() != props['value']:
                raise ValueError(f'Factory FIT integrity check failed: {node}')
    config = fit['/configurations/config-7020']
    fdt_name = config['fdt'].rstrip(b'\0').decode()
    fpga_name = config['fpga'].rstrip(b'\0').decode()
    fdt_path, fpga_path = '/images/'+fdt_name, '/images/'+fpga_name
    dt_original = fit[fdt_path]['data']
    dt = fdt_nodes(dt_original)
    if dt[''].get('model') != b'Alientek Navigator Zynq Development Board\0':
        raise ValueError('Unexpected platform')
    dt['']['bci,platform-id'] = b'ps-stream-bci1\0'
    if a.lcd_emio:
        if not a.implementation_status or 'LCD_EMIO=1' not in a.implementation_status.read_text():
            raise ValueError('LCD marker requires an LCD_EMIO=1 implementation report')
        dt['']['bci,lcd-id'] = b'emio-st7789-v1\0'
    if a.knob_emio:
        if not a.implementation_status or 'KNOB_EMIO=1' not in a.implementation_status.read_text():
            raise ValueError('EC11 marker requires KNOB_EMIO=1 implementation report')
        dt['']['bci,knob-id'] = b'emio-ec11-v1\0'
    cell = lambda *v: struct.pack('>'+str(len(v))+'I', *v)
    disabled = []
    for path, props in dt.items():
        if path.startswith('/amba_pl/') and path.count('/') == 2:
            props['status'] = b'disabled\0'
            disabled.append(path)
    # Disable root-level consumers of PL GPIO. Keep the PS-only ones active.
    pl_phandles = {int.from_bytes(props['phandle'], 'big') for path, props in dt.items()
                   if path.startswith('/amba_pl/') and 'phandle' in props}
    for parent in ('/leds', '/keys', '/beep'):
        if any(path.startswith(parent+'/') or path == parent for path in dt):
            for path, props in dt.items():
                if (path == parent or path.startswith(parent+'/')) and 'gpios' in props:
                    if int.from_bytes(props['gpios'][:4], 'big') in pl_phandles:
                        dt[parent]['status'] = b'disabled\0'
                        if parent not in disabled:
                            disabled.append(parent)
    # Keep all PS memory/MIO/USB/UART/SD configuration from the original DT.
    clock = next(props['phandle'] for path, props in dt.items()
                 if props.get('compatible') == b'xlnx,ps7-clkc\0')
    dt['/amba_pl']['status'] = b'okay\0'
    for name, base, compatible in [('bci_core', 0x43d00000, 'bci,ps-stream-core-v1'),
                                    ('bci_fifo', 0x43d10000, 'bci,tx-fifo-v1')]:
        dt[f'/amba_pl/{name}@{base:x}'] = {
            'compatible': compatible.encode()+b'\0', 'reg': cell(base, 0x10000),
            'clocks': clock+cell(15), 'clock-names': b's_axi_aclk\0', 'status': b'okay\0',
        }
    usb_path = '/amba/usb@e0002000'
    if dt[usb_path] != fdt_nodes(dt_original)[usb_path]:
        raise ValueError('USB properties unexpectedly changed')
    candidate = encode_fdt(dt, dt_original)
    a.output.mkdir(parents=True, exist_ok=False)
    (a.output/'bci-7020.dtb').write_bytes(candidate)
    report = {'factory_fit_sha256': hashlib.sha256(original).hexdigest(),
              'disabled_factory_pl_nodes': disabled,
              'core_base': '0x43d00000', 'fifo_base': '0x43d10000',
              'preserved_ps_usb_properties': True, 'ready_to_boot': False,
              'scope': 'Candidate offline files only; no board or TF modification'}
    if a.lcd_emio:
        report['lcd_backend'] = 'PS GPIO EMIO0..5 via J4 pins3..8; no DSP or spidev required'
    if a.bitstream:
        if not a.implementation_status or 'PASS IMPLEMENTATION_ONLY' not in a.implementation_status.read_text():
            raise ValueError('A passed implementation status is required for boot package generation')
        bits = a.bitstream.read_bytes()
        changed_fit = copy.deepcopy(fit)
        changed_fit[fdt_path]['data'] = candidate
        changed_fit[fpga_path]['data'] = gzip.compress(bits, mtime=0)
        changed_fit[fpga_path]['compression'] = b'gzip\0'
        changed_fit['/configurations']['default'] = b'config-7020\0'
        for node, props in changed_fit.items():
            if 'algo' in props and 'value' in props:
                parent = changed_fit[node.rsplit('/', 1)[0]]
                props['value'] = hashlib.new(props['algo'].rstrip(b'\0').decode(), parent['data']).digest()
        generated = encode_fdt(changed_fit, original)
        (a.output/'image.ub').write_bytes(generated)
        report.update({'ready_to_boot': True, 'bitstream_sha256': hashlib.sha256(bits).hexdigest(),
                       'required_boot_config': 'config-7020',
                       'warning': 'Use existing board BOOT.BIN; not yet board-validated'})
    (a.output/'preparation.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
