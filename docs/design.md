# Cloudflare architecture

The API Worker checks authentication, CORS and request sizes. It sends the request and verified identity to the store's Durable Object. Rust compiled to WebAssembly implements the TAMS routes, domain rules and background jobs. SQLite holds metadata and job state. Durable Object alarms resume copying, deletion and webhook delivery.

A media allocation returns a signed PUT URL and required headers. Clients transfer bytes directly to the configured private R2 backend. Registering a Segment checks the controlled Object. Media keys include the allocation identity so a later allocation of the same Object ID uses a different key.

Free configures Basic authentication. Paid adds an OAuth Worker whose private verification entry point checks bearer tokens. D1 stores the client registrations and encrypted signing keys. Both profiles deploy the same API modules.

The browser SDK implements the same TAMS rules using PostgreSQL in PGlite and browser-private files. The Demo and Playpen each host a complete browser runtime. The hosted API has its own store identity and lifecycle. [TASM source and browser architecture](https://github.com/livewyer-ops/tasm)
