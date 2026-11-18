# TLS transport ownership

Each connection has one response-reader thread. Request callers can send from
other threads, but they must not use the same SSL object simultaneously with
the reader or with another sender. OpenSSL permits passing an SSL connection
between threads when its operations do not overlap.

`SocketFrameTransport` serializes `settimeout` plus `recv` and `settimeout` plus
`sendall` with a per-connection condition. A pending writer has priority over
the next read. Individual receive operations wait at most 50 ms, so an idle
reader yields promptly to a command. This changes local socket scheduling,
not the frequency of protocol polling or the commands sent to the gate.

The write timeout is captured when the transport is constructed. Reader
polling cannot replace it with a shorter receive timeout. Waiting for the I/O
slot consumes that write budget; a caller that times out waiting never sends
the expired frame later. Errors release the slot and writer priority.

The sole reader owns the frame buffer. Incomplete frames are preserved across
reads, and a stream of fragments cannot extend one `read_frame` beyond its
overall deadline. The I/O slot is released between receive operations, so
fragmented frames do not monopolize the SSL object.

Descriptor lifetime is handled separately by `ResponseDispatcher`:

1. Mark the dispatcher closed and reject new exchanges.
2. Shut down the TCP side to wake blocked I/O without freeing the descriptor
   or detaching the SSL object. This wakeup deliberately runs outside the I/O
   condition; waiting for a blocked read's lock would prevent waking it.
3. Wait for the reader and admitted senders, including writers waiting for the
   I/O slot. If they remain active after the bounded wait, the last user closes
   the socket when it exits. An active descriptor is never released early.

TLS connection setup and handshake finish before the transport is handed to
the dispatcher. Final SSL socket close happens only after its users finish,
or from a reader callback when no socket I/O is active.

Regression coverage includes blocked and queued sends, simultaneous closes,
partial frames, write timeouts, and a real TLS session with concurrent callers
and pushed events. The original descriptor-lifetime tests are retained.

See the [OpenSSL thread-safety guidance](https://docs.openssl.org/master/man7/openssl-threads/).
