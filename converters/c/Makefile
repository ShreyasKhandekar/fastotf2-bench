# Makefile for the C OTF2 -> CSV converter (fastotf2-bench baseline).
#
# Uses otf2-config (from the OTF2 install, present in the bench container at
# /opt/otf2/bin) to discover include/lib/link flags. Falls back to /opt/otf2.

OTF2_CONFIG ?= otf2-config
CC          ?= gcc
CFLAGS      ?= -O3 -Wall

OTF2_CFLAGS  := $(shell $(OTF2_CONFIG) --cflags 2>/dev/null)
OTF2_LIBS    := $(shell $(OTF2_CONFIG) --libs 2>/dev/null)
OTF2_LDFLAGS := $(shell $(OTF2_CONFIG) --ldflags 2>/dev/null)

# Fallback if otf2-config is not on PATH.
ifeq ($(strip $(OTF2_CFLAGS)),)
OTF2_CFLAGS  := -I/opt/otf2/include
OTF2_LIBS    := -lotf2
OTF2_LDFLAGS := -L/opt/otf2/lib
endif

otf2csv: otf2csv.c
	$(CC) $(CFLAGS) $(OTF2_CFLAGS) -o $@ $< $(OTF2_LIBS) $(OTF2_LDFLAGS)

.PHONY: clean
clean:
	rm -f otf2csv
