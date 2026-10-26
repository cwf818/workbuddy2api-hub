"""Block-buffered SSE reading must be byte-identical to plain line iteration.

`for line in upstream` on an http.client.HTTPResponse costs one read() syscall
per SSE event, and every syscall releases and re-acquires the GIL. A streaming
turn emits thousands of events, so the process spends most of its time handing
the GIL between handler threads instead of working. Measured on the 4 vCPU box
running v1.7.2 at 15-20 req/s:

    10s syscall profile of wb-proxy (strace -c -f -p <pid>)
      71.44%   59.43s    559us/call   106214  futex     <- GIL handoff
      20.31%   16.89s  42659us/call      396  poll
       1.80%    1.50s     41us/call    36366  read      <- one per SSE line
       0.54%    0.45s     88us/call     5043  sendto

`iter_sse_lines()` reads with read1() (at most one underlying syscall per call)
and splits in memory, which is why the equivalence pinned here matters: the
whole point is to change the *number* of syscalls without changing a single
byte the caller sees.

The reference is io.BytesIO iteration, not a hand-rolled splitter, because that
is the same readline() semantics the replaced loop had: lines carry their
terminator, a final line without one is still yielded, and only "\\n" splits.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _isolated_dirs import isolated_data_dirs  # noqa: E402  (its own data root)
# Its own directories, removed when this process exits.
_TMP = isolated_data_dirs("wb-sse-read-batching-")

import wb_proxy as P  # noqa: E402

PASS = 0
FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] " + label + ("  " + str(detail) if detail else ""))


class FakeStream:
    """The wire as a list of chunks, answered through read1().

    `chunks` is handed over in order: read1() returns at most `n` bytes from the
    front of the current chunk and b"" once the script is exhausted. Chunks are
    deliberately not aligned to any block size, so lines straddle boundaries -
    the case the reader has to get right.
    """

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.pending = b""
        self.read1_calls = 0

    def read1(self, n=-1):
        self.read1_calls += 1
        if not self.pending:
            if not self.chunks:
                return b""
            self.pending = self.chunks.pop(0)
        if n is None or n < 0:
            out, self.pending = self.pending, b""
            return out
        out, self.pending = self.pending[:n], self.pending[n:]
        return out


def same_as_plain_iteration(data, chunk_size):
    """Read `data` in `chunk_size` chunks; return (yielded, read1_calls)."""
    wire = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]
    src = FakeStream(wire)
    got = list(P.iter_sse_lines(src))
    return got, src.read1_calls, len(wire)


print("-- equivalence with io.BytesIO iteration --")

CASES = [
    ("single event", b"data: {\"a\":1}\n\n"),
    ("two events, one block", b"data: {\"a\":1}\n\ndata: {\"b\":2}\n\n"),
    ("no trailing newline", b"data: {\"a\":1}\n\ndata: {\"b\":2}"),
    ("CRLF terminators", b"data: {\"a\":1}\r\n\r\ndata: {\"b\":2}\r\n\r\n"),
    ("blank lines and comments", b"\n: keepalive\n\ndata: x\n\n"),
    ("empty stream", b""),
    ("only a newline", b"\n"),
    ("no newline at all", b"data: {"),
    ("long single event", b"data: " + b"x" * 5000 + b"\n\n"),
    ("many events", b"".join(b"data: {\"i\":%d}\n\n" % i for i in range(300))),
]

for label, data in CASES:
    for chunk_size in (1, 7, 64, 4096):
        got, calls, wire = same_as_plain_iteration(data, chunk_size)
        ref = list(io.BytesIO(data))
        check("%s (chunk=%d) byte-identical" % (label, chunk_size),
              got == ref,
              "got=%r ref=%r" % (got[:4], ref[:4]))

print("-- the same bytes through the real io.BytesIO read1() --")
for label, data in CASES:
    got = list(P.iter_sse_lines(io.BytesIO(data)))
    check("%s via BytesIO" % label, got == list(io.BytesIO(data)),
          "got=%r" % (got[:4],))

print("-- a line is emitted as soon as it is complete, not when a block fills --")
src = FakeStream([b"data: one\n"])
gen = P.iter_sse_lines(src)
check("first complete line arrives on the first read1",
      next(gen) == b"data: one\n")
check("no second read1 was needed to emit it", src.read1_calls == 1,
      "%d calls" % src.read1_calls)

src = FakeStream([b"data: par", b"tial\n"])
got = list(P.iter_sse_lines(src))
check("a line straddling two chunks is reassembled", got == [b"data: partial\n"],
      repr(got))

print("-- syscall count: chunks, not lines --")

lines = b"".join(b"data: {\"i\":%d}\n\n" % i for i in range(2000))
ref = list(io.BytesIO(lines))
for chunk_size in (8192, 65536):
    got, calls, wire = same_as_plain_iteration(lines, chunk_size)
    check("%d-byte wire chunks stay byte-identical" % chunk_size, got == ref)
    check("%d-byte wire chunks: read1 calls bounded by wire chunks" % chunk_size,
          calls <= wire + 2,
          "%d calls for %d lines over %d chunks" % (calls, len(ref), wire))

# The headline number: what the old loop cost per line versus what it costs now.
_, calls, wire = same_as_plain_iteration(lines, 65536)
print("    2000 events / 64 KiB blocks: %d read1 calls vs %d lines (%.0fx fewer)"
      % (calls, len(ref), float(len(ref)) / max(1, calls)))
check("at least an order of magnitude fewer syscalls",
      len(ref) >= 10 * calls, "%d lines, %d calls" % (len(ref), calls))

print("-- objects without read1 fall back to plain iteration --")
check("a list iterates unchanged",
      list(P.iter_sse_lines([b"a\n", b"b\n"])) == [b"a\n", b"b\n"])
check("an iterator iterates unchanged",
      list(P.iter_sse_lines(iter([b"a\n", b"b"]))) == [b"a\n", b"b"])

src = FakeStream([b"y" * 100])
list(P.iter_sse_lines(src, block_size=16))
check("a custom block_size is passed to read1",
      src.read1_calls >= 7, "%d calls for 100 bytes at 16" % src.read1_calls)

print("-- strip_data_prefix still sees the shape it expects --")
got = list(P.iter_sse_lines(FakeStream([b"data: {\"a\": 1}\n\n", b"data: [DONE]\n\n"])))
check("prefix stripping works on the yielded lines",
      [P.strip_data_prefix(l.decode("utf-8", "replace")) for l in got]
      == ['{"a": 1}', "", "[DONE]", ""],
      repr(got))

print("-- before_read: the hook that lets a caller batch its writes --")

calls = [0]
src = FakeStream([b"a\n", b"b\n", b"c\n"])
got = list(P.iter_sse_lines(src, before_read=lambda: calls.__setitem__(0, calls[0] + 1)))
check("lines still come out intact with a hook attached",
      got == [b"a\n", b"b\n", b"c\n"], repr(got))
check("the hook fires exactly once per read1",
      calls[0] == src.read1_calls,
      "%d hook calls vs %d read1 calls" % (calls[0], src.read1_calls))

src = FakeStream([b"a\n", b"b\n"])
list(P.iter_sse_lines(src))
check("read1_calls is 3 for 2 chunks (the last one returns b'')",
      src.read1_calls == 3, src.read1_calls)

# The invariant that makes batching latency-free: whenever the generator is
# about to block on the socket, the caller's buffer has already been drained.
# Nothing sits in memory waiting for data that has not arrived.
drained = []
written = []
src = FakeStream([b"data: a\n", b"data: b\n", b"data: c\n"])
buf = []


def flush_out():
    if buf:
        written.append(b"".join(buf))
        del buf[:]


def before_read():
    # The hook runs immediately before read1(). Draining here is exactly what
    # makes batching latency-free, so record the size *after* the drain.
    flush_out()
    drained.append(len(buf))


for line in P.iter_sse_lines(src, before_read=before_read):
    buf.append(line)
flush_out()
check("the write buffer is empty whenever a blocking read begins",
      drained and all(n == 0 for n in drained), drained)
check("every event still reached the wire",
      b"".join(written) == b"data: a\ndata: b\ndata: c\n", written)


def batched(data, chunk_size):
    """Mimic _chat_stream_response: buffer events, flush on before_read."""
    wire = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)] or [b""]
    src = FakeStream(wire)
    writes = []
    pending = []

    def flush():
        if pending:
            writes.append(b"".join(pending))
            del pending[:]

    for line in P.iter_sse_lines(src, before_read=flush):
        pending.append(b"data: " + line)
    flush()
    return writes, len(wire)


print("-- batching: one write per read block, not one per event --")

events = b"".join(b"data: {\"i\":%d}\n\n" % i for i in range(2000))
expected = b"".join(b"data: " + l for l in io.BytesIO(events))
for chunk_size in (8192, 65536):
    writes, wire = batched(events, chunk_size)
    check("%d-byte blocks: batched output is byte-identical" % chunk_size,
          b"".join(writes) == expected)
    check("%d-byte blocks: at most one write per block" % chunk_size,
          len(writes) <= wire + 1,
          "%d writes over %d blocks" % (len(writes), wire))

writes, wire = batched(events, 65536)
n_events = len(list(io.BytesIO(events)))
print("    4000 SSE lines / 64 KiB blocks: %d writes vs %d unbuffered writes (%.0fx fewer)"
      % (len(writes), n_events, float(n_events) / max(1, len(writes))))
check("batching removes at least an order of magnitude of writes",
      n_events >= 10 * len(writes),
      "%d lines, %d writes" % (n_events, len(writes)))

print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
