# Use the compiler supplied by the board's ARMv7 Linux SDK.
# make CROSS_COMPILE=arm-linux-gnueabihf-
# For an SDK environment exporting CC/CFLAGS: source SDK, then make.
ifeq ($(origin CC),default)
CC = $(CROSS_COMPILE)gcc
endif
CFLAGS ?= -O2 -Wall -Wextra -Wpedantic -std=c11
LDFLAGS ?=
LDLIBS ?=

rf_serial_rx: rf_serial_rx.c cyton_parser.c cyton_parser.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ rf_serial_rx.c cyton_parser.c $(LDLIBS)

pl_sample_replay: pl_sample_replay.c cyton_parser.c cyton_parser.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ pl_sample_replay.c cyton_parser.c $(LDLIBS)

pl_live_rx: pl_live_rx.c pl_sample_replay.c cyton_parser.c cyton_parser.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ pl_live_rx.c cyton_parser.c $(LDLIBS)

st7789_display: st7789_display.c st7789_emio.h bci_zh_font.h bci_storage_guard.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -finput-charset=UTF-8 -fexec-charset=UTF-8 $(LDFLAGS) -o $@ st7789_display.c $(LDLIBS)

bci_app: bci_app.c bci_rotary.h bci_ui_status.h bci_menu.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ bci_app.c $(LDLIBS)

bci_run: bci_run.c bci_notch.h bci_ui_status.h pl_sample_replay.c cyton_parser.c cyton_parser.h
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -o $@ bci_run.c cyton_parser.c $(LDLIBS) -lm

.PHONY: clean
clean:
	$(RM) rf_serial_rx rf_serial_rx.exe pl_sample_replay pl_sample_replay.exe pl_live_rx pl_live_rx.exe st7789_display st7789_display.exe bci_app bci_app.exe bci_run bci_run.exe
