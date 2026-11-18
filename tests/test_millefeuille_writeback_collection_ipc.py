"""Actual worker pipes and parent decoder; no DNS or real API."""

import sys
import time
import unittest
from unittest.mock import patch

from millefeuille.domain import writeback_http_process as process
from millefeuille.domain.writeback_http import _AmbiguousWriteError
from millefeuille.domain.writeback_live_collection import CollectionReader
from tests import test_millefeuille_writeback_http_process as base

SOURCE = """
from millefeuille.domain import writeback_http as h
from millefeuille.domain.writeback_live_collection import project_collection
from millefeuille.domain.writeback_http_worker import _main
async def synthetic(self,method,identity,deadline,**kwargs):
    assert method=='GET-COLLECTION'
    raw=h._wire({'key':identity['key'],'version':7,
       'library':{'type':identity['library_type'],'id':int(identity['library_id'])},
       'data':{'key':identity['key'],'version':7,'name':'Collection','parentCollection':False}})
    snapshot,proof=project_collection(identity,raw,7)
    value={'snapshot':snapshot,'proof':proof}
    REPLACE
    return value
h._HTTPBoundary._exchange=synthetic
_main()
"""


class CollectionIPCTests(unittest.TestCase):
    setUp = base.ProcessHTTPTests.setUp
    assert_unsupported_platform = base.ProcessHTTPTests.assert_unsupported_platform
    worker = base.ProcessHTTPTests.worker
    assert_reaped = base.ProcessHTTPTests.assert_reaped

    def invoke(self, change="pass"):
        def spawn(*args, **kwargs):
            child = self.original_popen(*args, **kwargs)
            self.processes.append(child)
            return child

        with (
            patch.object(
                process,
                "_worker_command",
                return_value=self.worker(SOURCE.replace("REPLACE", change)),
            ),
            patch.object(process.subprocess, "Popen", side_effect=spawn),
        ):
            try:
                return CollectionReader("DUMMYSECRET").read_collection(
                    self.identity, deadline=time.monotonic() + 1.5
                )
            finally:
                self.assert_reaped()

    def test_valid_default_collection_and_once_only(self):
        if sys.platform != "linux":
            self.assert_unsupported_platform()
            return
        self.assertEqual(self.invoke()["snapshot"]["version"], 7)
        reader = CollectionReader("DUMMYSECRET")
        with patch.object(
            process,
            "_worker_command",
            return_value=self.worker(SOURCE.replace("REPLACE", "pass")),
        ):
            reader.read_collection(self.identity, deadline=time.monotonic() + 1.5)
            with patch.object(process.subprocess, "Popen") as spawn:
                with self.assertRaises(ValueError):
                    reader.read_collection(self.identity, deadline=time.monotonic() + 1)
                spawn.assert_not_called()

    def test_parent_rejects_tampered_collection_proofs(self):
        if sys.platform != "linux":
            self.assert_unsupported_platform()
            return
        cases = {
            "missing": "value={}",
            "malformed": "value=None",
            "hash": "proof['raw_sha256']='sha256:'+'0'*64",
            "semantic": "snapshot['name']='Other'",
            "version": "proof['header_version']=8",
            "identity-key": "proof['identity']['key']='ZZZZ9999'",
            "wrong-library-valid-proof": (
                "other={**identity,'library_id':'999'};"
                "raw=raw.replace(b'12345',b'999');"
                "snapshot,proof=project_collection(other,raw,7);"
                "value={'snapshot':snapshot,'proof':proof}"
            ),
            "nonfinite": "proof['raw_utf8']=proof['raw_utf8'].replace('7','NaN',1)",
            "duplicate": (
                "proof['raw_utf8']='{'+chr(34)+'key'+chr(34)+':'+"
                "chr(34)+'ABCD1234'+chr(34)+','+proof['raw_utf8'][1:]"
            ),
            "oversize": "proof['raw_utf8']=' '*65537",
        }
        for name, change in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError) as caught:
                self.invoke(change)
            self.assertNotIsInstance(caught.exception, _AmbiguousWriteError)

    def test_unknown_and_expired_deny_before_spawn(self):
        if sys.platform != "linux":
            self.assert_unsupported_platform()
            return
        with patch.object(process.subprocess, "Popen") as spawn:
            for method, deadline in [
                ("UNKNOWN", time.monotonic() + 1),
                ("GET-COLLECTION", time.monotonic() - 1),
            ]:
                with self.assertRaises(ValueError):
                    process._contained_exchange(
                        "DUMMYSECRET", method, self.identity, deadline
                    )
            spawn.assert_not_called()

    def test_collection_validation_keeps_inherited_deadline(self):
        if sys.platform != "linux":
            self.assert_unsupported_platform()
            return
        from millefeuille.domain import writeback_live_collection as collection

        original = collection.checked_collection

        def slow(value, expected):
            result = original(value, expected)
            time.sleep(1.5)
            return result

        with (
            patch.object(collection, "checked_collection", side_effect=slow),
            self.assertRaisesRegex(ValueError, "deadline expired"),
        ):
            self.invoke()

    def test_oversized_ipc_response_is_reaped(self):
        if sys.platform != "linux":
            self.assert_unsupported_platform()
            return
        source = (
            "import sys,struct;sys.stdin.buffer.read();"
            "sys.stdout.buffer.write(struct.pack('!I',200001)+b'x'*200001);"
            "sys.stdout.buffer.flush()"
        )

        def spawn(*args, **kwargs):
            child = self.original_popen(*args, **kwargs)
            self.processes.append(child)
            return child

        with (
            patch.object(process, "_worker_command", return_value=self.worker(source)),
            patch.object(process.subprocess, "Popen", side_effect=spawn),
        ):
            with self.assertRaises(ValueError) as caught:
                CollectionReader("DUMMYSECRET").read_collection(
                    self.identity, deadline=time.monotonic() + 1.5
                )
            self.assertNotIsInstance(caught.exception, _AmbiguousWriteError)
        self.assert_reaped()
