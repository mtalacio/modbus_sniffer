import unittest
from modbus_sniffer import Framer, crc16


def frame(hex_data):
    payload = bytes.fromhex(hex_data)
    return payload + crc16(payload).to_bytes(2, "little")


class FramerTests(unittest.TestCase):
    def test_known_crc(self):
        self.assertEqual(crc16(bytes.fromhex("01 03 00 00 00 0a")), 0xCDC5)

    def test_fragmented_and_coalesced(self):
        request = frame("01 03 00 00 00 02")
        response = frame("01 03 04 00 7b 01 c8")
        for split in range(len(request + response) + 1):
            decoder = Framer()
            data = request + response
            events = decoder.feed(data[:split]) + decoder.feed(data[split:])
            self.assertEqual([e["type"] for e in events], ["frame", "frame"])
            self.assertEqual(events[1]["registers_uint16"], [123, 456])
            self.assertEqual(decoder.flush(), [])

    def test_bad_crc_preserved_and_recovery(self):
        bad = bytearray(frame("01 03 00 00 00 02"))
        bad[-1] ^= 1
        decoder = Framer()
        events = decoder.feed(bad + frame("02 83 02"))
        self.assertEqual(events[0]["type"], "unclassified")
        self.assertEqual(bytes.fromhex(events[0]["hex"]), bytes(bad))
        self.assertEqual(events[1]["exception_code"], 2)

    def test_echo_is_ambiguous(self):
        event = Framer().feed(frame("01 06 00 01 00 05"))[0]
        self.assertEqual(event["interpretations"], ["request_or_echo_response"])

    def test_multiple_register_write_and_broadcast(self):
        event = Framer().feed(frame("00 10 00 0a 00 02 04 00 07 00 09"))[0]
        self.assertEqual(event["registers_uint16"], [7, 9])
        self.assertEqual(event["address"], 0)

    def test_bits(self):
        event = Framer().feed(frame("01 01 01 05"))[0]
        self.assertEqual(event["bits_lsb_first_including_padding"], [1, 0, 1, 0, 0, 0, 0, 0])

    def test_unknown_and_partial_retained(self):
        decoder = Framer()
        data = frame("01 07 00") + b"\x01\x03"
        events = decoder.feed(data) + decoder.flush()
        self.assertEqual(b"".join(bytes.fromhex(e["hex"]) for e in events), data)
        self.assertTrue(all(e["type"] == "unclassified" for e in events))

    def test_buffer_bounded_and_lossless(self):
        decoder = Framer()
        data = b"\xff" * 4096
        events = decoder.feed(data)
        self.assertLessEqual(len(decoder.buffer), 512)
        events += decoder.flush()
        self.assertEqual(b"".join(bytes.fromhex(e["hex"]) for e in events), data)


if __name__ == "__main__":
    unittest.main()
