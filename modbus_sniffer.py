#!/usr/bin/env python3
"""Receive-only Modbus RTU capture. Python 3.9+, pySerial 3.5."""
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import os
import sys
import time


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def candidates(data):
    """Plausible (length, interpretation) pairs; direction is never measured."""
    if len(data) < 2 or data[0] > 247:
        return []
    address, function = data[:2]
    result = []
    if 0x81 <= function <= 0xFF and address:
        return [(5, "exception")]
    if function in (1, 2, 3, 4):
        if len(data) >= 6:
            count = int.from_bytes(data[4:6], "big")
            limit = 2000 if function in (1, 2) else 125
            if 1 <= count <= limit and address:
                result.append((8, "request"))
        if len(data) >= 3 and address:
            size = data[2]
            if 1 <= size <= 250 and (function in (1, 2) or size % 2 == 0):
                result.append((size + 5, "response"))
    elif function in (5, 6):
        if function == 6 or (len(data) >= 6 and data[4:6] in (b"\x00\x00", b"\xff\x00")):
            result.append((8, "request_or_echo_response"))
    elif function in (15, 16) and len(data) >= 6:
        count = int.from_bytes(data[4:6], "big")
        if 1 <= count <= (1968 if function == 15 else 123):
            if address:
                result.append((8, "response"))
            if len(data) >= 7:
                expected = (count + 7) // 8 if function == 15 else count * 2
                if data[6] == expected:
                    result.append((9 + expected, "request"))
    return result


def decode(frame, roles):
    address, function = frame[:2]
    info = {"address": address, "function": function,
            "interpretations": roles, "direction": "not_measured"}
    if function & 0x80:
        info.update(original_function=function & 0x7F, exception_code=frame[2])
    elif len(roles) == 1:
        role = roles[0]
        if function in (1, 2, 3, 4) and role == "response":
            payload = frame[3:-2]
            if function in (3, 4):
                info["registers_uint16"] = [int.from_bytes(payload[i:i+2], "big")
                                            for i in range(0, len(payload), 2)]
            else:
                info["bits_lsb_first_including_padding"] = [
                    (byte >> bit) & 1 for byte in payload for bit in range(8)]
        else:
            info["start_address_zero_based"] = int.from_bytes(frame[2:4], "big")
            info["value" if function in (5, 6) else "quantity"] = int.from_bytes(frame[4:6], "big")
            if function == 16 and role == "request":
                info["registers_uint16"] = [int.from_bytes(frame[i:i+2], "big")
                                            for i in range(7, len(frame)-2, 2)]
    return info


class Framer:
    """Heuristic stream recovery, not a timing-compliant RTU receiver.

    Unrecognized bytes are retained until idle or buffer limit. CRC collisions
    and unsupported functions can produce ambiguous boundaries.
    """
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        events = []
        while self.buffer:
            match = None
            for offset in range(len(self.buffer) - 3):
                data = self.buffer[offset:offset + 256]
                valid = [(length, role) for length, role in candidates(data)
                         if length <= len(data) and crc16(data[:length]) == 0]
                if valid:
                    length = min(length for length, _ in valid)
                    roles = [role for size, role in valid if size == length]
                    match = offset, length, roles
                    break
            if match is None:
                if len(self.buffer) > 512:
                    count = len(self.buffer) - 256
                    events.append(self._unknown(bytes(self.buffer[:count]), "buffer_limit"))
                    del self.buffer[:count]
                break
            offset, length, roles = match
            if offset:
                events.append(self._unknown(bytes(self.buffer[:offset]), "resynchronization"))
            frame = bytes(self.buffer[offset:offset + length])
            del self.buffer[:offset + length]
            events.append({"type": "frame", "hex": frame.hex(" "), "crc_valid": True,
                           **decode(frame, roles)})
        return events

    @staticmethod
    def _unknown(data, reason):
        return {"type": "unclassified", "hex": data.hex(" "), "reason": reason}

    def flush(self, reason="host_idle"):
        if not self.buffer:
            return []
        data = bytes(self.buffer)
        self.buffer.clear()
        return [self._unknown(data, reason)]


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("deve ser um número positivo e finito")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sniffer Modbus RTU: apenas receção, sem pedidos no bus.")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--parity", choices=("N", "E", "O"), default="E")
    parser.add_argument("--stopbits", type=int, choices=(1, 2), default=1)
    parser.add_argument("--idle-ms", type=positive_float, default=100,
                        help="limpeza de fragmentos por inatividade no host; não é t3.5 (100 ms)")
    parser.add_argument("--log", help="ficheiro JSONL em append; inclui todos os blocos recebidos")
    parser.add_argument("--list-ports", action="store_true")
    args = parser.parse_args(argv)
    if args.baud <= 0:
        parser.error("--baud deve ser positivo")
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("Instale pySerial: python3 -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    if args.list_ports:
        for port in list_ports.comports():
            print(port.device, port.description)
        return 0

    counts = Counter()
    framer = Framer()
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            log = stack.enter_context(open(args.log, "a", encoding="utf-8", buffering=1)) if args.log else None

            def emit(event):
                event = {"host_time_utc": datetime.now(timezone.utc).isoformat(),
                         "host_elapsed_s": round(time.monotonic() - started, 6), **event}
                counts[event["type"]] += 1
                line = json.dumps(event, ensure_ascii=False)
                if log:
                    log.write(line + "\n")
                if event["type"] != "rx_chunk":
                    print(line, flush=True)

            # Never call write(), send_break() or enable RS485 transmit emulation.
            port = serial.Serial(port=None, baudrate=args.baud, bytesize=8,
                                 parity=args.parity, stopbits=args.stopbits,
                                 timeout=0.01, xonxoff=False, rtscts=False, dsrdtr=False,
                                 exclusive=True if os.name == "posix" else None)
            stack.callback(port.close)
            port.rts = False
            port.dtr = False
            port.port = args.port
            port.open()
            emit({"type": "session", "port": args.port, "baud": args.baud,
                  "parity": args.parity, "stopbits": args.stopbits, "idle_ms": args.idle_ms,
                  "framing": "length_and_crc_heuristic"})
            last_rx = time.monotonic()
            try:
                while True:
                    chunk = port.read(min(max(port.in_waiting, 1), 4096))
                    now = time.monotonic()
                    if now - last_rx >= args.idle_ms / 1000:
                        for event in framer.flush():
                            emit(event)
                    if chunk:
                        last_rx = now
                        counts["bytes"] += len(chunk)
                        emit({"type": "rx_chunk", "hex": chunk.hex(" ")})
                        for event in framer.feed(chunk):
                            emit(event)
            except KeyboardInterrupt:
                pass
            finally:
                for event in framer.flush("capture_end"):
                    emit(event)
                emit({"type": "summary", "counts": dict(counts)})
    except (serial.SerialException, OSError) as exc:
        print("Erro de captura: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
