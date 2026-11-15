"""Execute only the AST-extracted stream class, never child startup or PDFs."""

import ast
import io
from pathlib import Path
from types import SimpleNamespace
import unittest


def stream_class(source=None, cap=4096, backing=io.BytesIO):
    if source is None:
        source = (
            Path(__file__).parents[1] / "millefeuille/domain/native_decode_boundary.py"
        ).read_text()
    module = ast.parse(source)
    child = next(
        n.value
        for n in module.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_CHILD" for t in n.targets)
    )
    tree = ast.parse(ast.literal_eval(child))
    compile(tree, "<child-compile-only>", "exec")
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "BoundedMemory"
    )
    namespace = {
        "io": SimpleNamespace(BytesIO=backing),
        "read_cap": cap,
        "raw": b"x" * 1024,
    }
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]), "<standalone-stream-only>", "exec"
        ),
        namespace,
    )
    return namespace["BoundedMemory"]


def recovery_scan(stream):
    # Exact interface choice used at all three PyPDF2 3.0.1 recovery sites.
    if hasattr(stream, "getbuffer"):
        return bytes(stream.getbuffer())
    position = stream.tell()
    stream.seek(0, 0)
    data = stream.read(-1)
    stream.seek(position, 0)
    return data


class StreamBudgetTests(unittest.TestCase):
    def test_recovery_replay_refuses_fifth_scan(self):
        stream = stream_class()(b"x" * 1024)
        stream.seek(17)
        for _ in range(4):
            self.assertEqual(len(recovery_scan(stream)), 1024)
            self.assertEqual(stream.tell(), 17)
        with self.assertRaisesRegex(ValueError, "logical-read-budget-exhausted"):
            recovery_scan(stream)
        self.assertEqual(stream.charged, 4096)

    def test_no_raw_buffer_or_write_routes(self):
        stream = stream_class()(b"abc")
        for name in (
            "getbuffer",
            "getvalue",
            "buffer",
            "raw",
            "write",
            "writelines",
            "truncate",
            "fileno",
        ):
            self.assertFalse(hasattr(stream, name), name)
        with self.assertRaises(TypeError):
            memoryview(stream)
        self.assertFalse(stream.writable())

    def test_all_read_routes_refuse_before_backing_access(self):
        calls = []

        class Spy(io.BytesIO):
            def read(self, n=-1):
                calls.append("read")
                return super().read(n)

            def readline(self, n=-1):
                calls.append("readline")
                return super().readline(n)

            def readinto(self, buffer):
                calls.append("readinto")
                return super().readinto(buffer)

        operations = (
            lambda s: s.read(3),
            lambda s: s.read1(3),
            lambda s: s.readinto(bytearray(3)),
            lambda s: s.readinto1(bytearray(3)),
            lambda s: s.readline(3),
            lambda s: s.readlines(),
            lambda s: next(s),
        )
        for operation in operations:
            stream = stream_class(cap=2, backing=Spy)(b"a\nb")
            calls.clear()
            with self.assertRaisesRegex(ValueError, "logical-read-budget-exhausted"):
                operation(stream)
            self.assertEqual(calls, [])
            self.assertEqual((stream.tell(), stream.charged), (0, 0))

    def test_mixed_routes_and_no_remaining_budget(self):
        stream = stream_class(cap=8)(b"abcdefgh")
        self.assertEqual(stream.read(1), b"a")
        self.assertEqual(stream.read1(1), b"b")
        buf = bytearray(2)
        self.assertEqual(stream.readinto(buf), 2)
        self.assertEqual(buf, b"cd")
        self.assertEqual(stream.readinto1(buf), 2)
        self.assertEqual(buf, b"ef")
        self.assertEqual(stream.readline(2), b"gh")
        self.assertEqual(stream.charged, 8)
        self.assertEqual(stream.read(100), b"")
        stream.seek(0)
        self.assertEqual(stream.read(0), b"")
        self.assertEqual(stream.readline(0), b"")
        self.assertEqual(stream.readinto(bytearray()), 0)
        with self.assertRaises(ValueError):
            stream.read(1)
        self.assertEqual(stream.tell(), 0)

    def test_partial_eof_and_seek_size_do_not_overcharge(self):
        stream = stream_class(cap=2)(b"abcd")
        self.assertEqual(stream.seek(0, 2), 4)
        self.assertEqual(stream.seek(-2, 1), 2)
        self.assertEqual(stream.read(1000), b"cd")
        self.assertEqual(stream.charged, 2)
        stream.seek(100)
        self.assertEqual(stream.readline(), b"")
        self.assertEqual(stream.readinto(bytearray(9)), 0)
        self.assertEqual(stream.charged, 2)
        with self.assertRaises(ValueError):
            stream.seek(-1)

    def test_partial_readinto_preserves_tail_and_counts_bytes(self):
        stream = stream_class(cap=3)(b"abc")
        buf = bytearray(b"?????")
        self.assertEqual(stream.readinto1(buf), 3)
        self.assertEqual(buf, b"abc??")
        self.assertEqual(stream.charged, 3)
        stream = stream_class(cap=4)(b"abcd")
        typed = memoryview(bytearray(4)).cast("H")
        self.assertEqual(stream.readinto(typed), 4)
        self.assertEqual(typed.cast("B").tobytes(), b"abcd")

    def test_line_reservation_is_conservative_and_shared(self):
        stream = stream_class(cap=6)(b"a\nb\n")
        self.assertEqual(stream.readline(), b"a\n")
        self.assertEqual(stream.charged, 4)
        self.assertEqual(next(stream), b"b\n")
        self.assertEqual(stream.charged, 6)
        with self.assertRaises(StopIteration):
            next(stream)
        stream = stream_class(cap=6)(b"a\nb\n")
        self.assertEqual(stream.readlines(), [b"a\n", b"b\n"])
        self.assertEqual(stream.charged, 6)
        stream = stream_class(cap=4)(b"a\nb\n")
        self.assertEqual(stream.readlines(1), [b"a\n"])
        with self.assertRaises(ValueError):
            next(stream)
        self.assertEqual(stream.tell(), 2)

    def test_empty_closed_and_independent_instances(self):
        cls = stream_class(cap=0)
        stream = cls(b"")
        self.assertEqual(list(stream), [])
        self.assertEqual(stream.read(), b"")
        self.assertEqual(stream.charged, 0)
        stream.close()
        self.assertTrue(stream.closed)
        with self.assertRaises(ValueError):
            stream.read()
        cls = stream_class(cap=1)
        first, second = cls(b"a"), cls(b"b")
        self.assertEqual(first.read(), b"a")
        self.assertEqual(second.read(), b"b")


if __name__ == "__main__":
    unittest.main()
