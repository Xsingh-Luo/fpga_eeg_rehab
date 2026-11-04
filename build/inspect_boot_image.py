"""Read an MBR/FAT32 boot partition without mounting or modifying the image."""
import argparse
import hashlib
import gzip
import json
from pathlib import Path
import struct


def fdt_nodes(blob):
    if len(blob) < 40 or blob[:4] != bytes.fromhex('d00dfeed'):
        raise ValueError('Not a flattened device tree')
    _, total, start, strings = struct.unpack_from('>4I', blob)
    if total > len(blob):
        raise ValueError('Truncated FDT')
    pos, stack, nodes = start, [], {}
    while pos < total:
        token, = struct.unpack_from('>I', blob, pos)
        pos += 4
        if token == 1:
            end = blob.index(0, pos)
            stack.append(blob[pos:end].decode())
            pos = (end+4) & ~3
            nodes['/'.join(stack)] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, nameoff = struct.unpack_from('>2I', blob, pos)
            pos += 8
            name_end = blob.index(0, strings+nameoff)
            name = blob[strings+nameoff:name_end].decode()
            nodes['/'.join(stack)][name] = blob[pos:pos+length]
            pos = (pos+length+3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            return nodes
        else:
            raise ValueError(f'Unexpected FDT token {token}')
    raise ValueError('Missing FDT end marker')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    with a.image.open('rb') as image:
        mbr = image.read(512)
        if mbr[510:] != b'\x55\xaa' or mbr[450] not in (0xb, 0xc):
            raise ValueError('Expected FAT32 MBR partition 1')
        partition = struct.unpack_from('<I', mbr, 454)[0] * 512
        image.seek(partition)
        bpb = image.read(512)
        sector, = struct.unpack_from('<H', bpb, 11)
        cluster_sectors, = struct.unpack_from('<B', bpb, 13)
        reserved, = struct.unpack_from('<H', bpb, 14)
        n_fats, = struct.unpack_from('<B', bpb, 16)
        fat_sectors, = struct.unpack_from('<I', bpb, 36)
        root_cluster, = struct.unpack_from('<I', bpb, 44)
        cluster_bytes = sector * cluster_sectors
        if sector != 512 or not cluster_bytes or not fat_sectors:
            raise ValueError('Unsupported FAT geometry')
        image.seek(partition+reserved*sector)
        fat = image.read(fat_sectors*sector)
        data_start = partition + (reserved+n_fats*fat_sectors)*sector

        def chain(first):
            cluster, seen = first, set()
            while 2 <= cluster < 0xffffff8:
                if cluster in seen or cluster*4+4 > len(fat):
                    raise ValueError('Invalid FAT chain')
                seen.add(cluster)
                image.seek(data_start+(cluster-2)*cluster_bytes)
                yield image.read(cluster_bytes)
                cluster = struct.unpack_from('<I', fat, cluster*4)[0] & 0xfffffff

        directory = b''.join(chain(root_cluster))
        listing, extracted = [], {}
        for offset in range(0, len(directory), 32):
            entry = directory[offset:offset+32]
            if not entry[0]:
                break
            if entry[0] == 0xe5 or entry[11] & 0x18 or entry[11] == 0xf:
                continue
            base = entry[:8].decode('ascii').strip()
            ext = entry[8:11].decode('ascii').strip()
            name = base + ('.'+ext if ext else '')
            first = struct.unpack_from('<H', entry, 20)[0]*65536 + struct.unpack_from('<H', entry, 26)[0]
            size, = struct.unpack_from('<I', entry, 28)
            listing.append({'short_name': name, 'bytes': size})
            if name.upper() in ('BOOT.BIN', 'IMAGE.UB', 'SYSTEM.DTB', 'BOOT.SCR', 'UENV.TXT', 'EXTLINUX.CON'):
                blob = b''.join(chain(first))[:size]
                if len(blob) != size:
                    raise ValueError('Truncated boot file')
                (a.output/name.lower()).write_bytes(blob)
                extracted[name] = {'bytes': size, 'sha256': hashlib.sha256(blob).hexdigest()}
        trees, fit_checks = [], {}
        for path in list(a.output.iterdir()):
            blob = path.read_bytes()
            if blob[:4] != bytes.fromhex('d00dfeed'):
                continue
            tree = fdt_nodes(blob)
            for node, props in tree.items():
                if 'algo' in props and 'value' in props:
                    parent = tree[node.rsplit('/', 1)[0]]
                    algorithm = props['algo'].rstrip(b'\0').decode()
                    if hashlib.new(algorithm, parent['data']).digest() != props['value']:
                        raise ValueError(f'FIT integrity check failed: {node}')
                    fit_checks[node] = algorithm + ':PASS'
                if props.get('type') == b'flat_dt\0' and 'data' in props:
                    target = a.output/(node.rsplit('/', 1)[-1] + '.dtb')
                    target.write_bytes(props['data'])
                    trees.append((target, fdt_nodes(props['data'])))
                if props.get('type') == b'fpga\0' and 'data' in props:
                    data = props['data']
                    if props.get('compression') == b'gzip\0':
                        data = gzip.decompress(data)
                    elif props.get('compression', b'none\0') != b'none\0':
                        raise ValueError('Unsupported FPGA compression')
                    target = a.output/(node.rsplit('/', 1)[-1] + '.bit')
                    target.write_bytes(data)
                    extracted[target.name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            if path.suffix == '.dtb':
                trees.append((path, tree))
        for path, tree in trees:
            # Preserve raw property bytes as hex; do not guess units or cell counts.
            serializable = {node: {key: value.hex() for key, value in props.items()}
                            for node, props in tree.items()}
            (a.output/(path.stem+'_properties.json')).write_text(json.dumps(serializable, indent=2))
        report = {'image': str(a.image.resolve()), 'image_modified': False,
                  'boot_files': listing, 'extracted': extracted, 'device_trees': [p.name for p,_ in trees]}
        report['fit_integrity_checks'] = fit_checks
        (a.output/'inspection.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
