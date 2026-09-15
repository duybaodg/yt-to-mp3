# Tunecast Interview Preparation Guide

This guide explains the project as it exists today and provides project-specific
answers for software engineering, backend, full-stack, DevOps, security, and
behavioral interviews.

## Table of contents

1. [Thirty-second introduction](#thirty-second-introduction)
2. [Two-minute project walkthrough](#two-minute-project-walkthrough)
3. [Architecture](#architecture)
4. [Current technology stack](#current-technology-stack)
5. [Important numbers to remember](#important-numbers-to-remember)
6. [Project and product questions](#project-and-product-questions)
7. [API and request-flow questions](#api-and-request-flow-questions)
8. [Concurrency and Cloudflare 524 questions](#concurrency-and-cloudflare-524-questions)
9. [Redis and RQ questions](#redis-and-rq-questions)
10. [yt-dlp and ffmpeg questions](#yt-dlp-and-ffmpeg-questions)
11. [Security questions](#security-questions)
12. [Docker, Nginx, Cloudflare, and deployment questions](#docker-nginx-cloudflare-and-deployment-questions)
13. [Testing and reliability questions](#testing-and-reliability-questions)
14. [Frontend and accessibility questions](#frontend-and-accessibility-questions)
15. [Scaling and system-design questions](#scaling-and-system-design-questions)
16. [Behavioral questions](#behavioral-questions)
17. [Honest limitations and future improvements](#honest-limitations-and-future-improvements)
18. [Mock interview](#mock-interview)
19. [Final rehearsal checklist](#final-rehearsal-checklist)

## Thirty-second introduction

> I built Tunecast, a Flask application that converts YouTube videos and
> playlists into MP3 files. The browser submits a job, Redis stores its state,
> and an RQ worker runs yt-dlp and ffmpeg outside the HTTP request. The frontend
> polls for completion and downloads the generated file. I added per-IP rate
> limiting, an atomic global queue cap, SSRF protection, media limits, temporary
> storage, Docker deployment, Nginx and Cloudflare proxy handling, and automated
> security tests.

## Two-minute project walkthrough

> The first version performed the entire conversion synchronously inside a
> Flask request. In production, Gunicorn had two synchronous workers. When more
> than a few users submitted conversions together, both workers were occupied
> by yt-dlp and ffmpeg, later requests waited without receiving a response, and
> Cloudflare returned 524 timeouts.
>
> I fixed the root cause by moving both metadata extraction and conversion into
> Redis-backed RQ jobs. The API now validates the request, atomically reserves a
> global capacity slot, enqueues a job, and immediately returns `202 Accepted`
> with a UUID and polling URL. A dedicated worker downloads and converts the
> media. The browser polls the job endpoint and requests the finished MP3 from a
> separate download endpoint.
>
> The system protects itself at several layers. It limits requests per IP,
> admits at most ten outstanding jobs globally, limits playlist length, video
> duration, download size, final MP3 size, request-body size, and temporary
> storage. URLs must use HTTPS and an approved YouTube hostname, and resolved
> addresses cannot be private or loopback. Nginx restores the real visitor IP
> only from trusted Cloudflare networks. Docker runs the app as a non-root user,
> and Redis, the worker, and Gunicorn have resource limits.
>
> The test suite uses mocked media downloads so CI remains deterministic. It
> currently has 37 passing tests. Docker integration testing also found that the
> RQ worker inherited the web container's HTTP health check. I fixed that by
> disabling the web-specific health check for the worker service while keeping
> the Gunicorn health check enabled.

## Architecture

```text
Browser
  |
  | POST /playlist/info or /convert
  v
Cloudflare
  |
  v
Nginx
  |
  v
Gunicorn + Flask
  |-- validate request and URL
  |-- apply per-IP rate limit
  |-- reserve a global Redis capacity slot
  |-- enqueue an RQ job
  `-- return 202 + UUID + status URL
                  |
                  v
               Redis/RQ
                  |
                  v
             RQ worker
             |-- validate again
             |-- yt-dlp download
             |-- ffmpeg conversion
             `-- save UUID.mp3
                  |
Browser polls GET /jobs/<uuid>
                  |
                  v
Browser requests GET /downloads/<uuid>
                  |
                  v
            MP3 sent and removed
```

### Single-video flow

1. The user submits a URL.
2. The browser posts it to `/playlist/info`.
3. Flask validates it and queues a metadata job.
4. The browser polls `/jobs/<uuid>`.
5. The metadata job reports that the URL represents one video.
6. The browser posts the URL to `/convert`.
7. Flask validates it, reserves capacity, and queues a conversion job.
8. The worker downloads the best audio and converts it to a 192 kbps MP3.
9. The browser polls until the job is ready.
10. The browser requests `/downloads/<uuid>`.
11. Flask sends the MP3 and removes its temporary files.

### Playlist flow

1. The metadata job extracts a flat playlist without downloading its tracks.
2. The API returns at most 50 tracks.
3. The frontend displays one download button per track.
4. Clicking a track creates an independent conversion job for that video.
5. Per-IP and global limits still apply to every conversion.

## Current technology stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| Web backend | Flask | Routes, validation, errors, and file responses |
| WSGI server | Gunicorn | Production Flask process management |
| Queue | RQ | Background job execution |
| Shared state | Redis | RQ, rate-limit counters, and capacity reservations |
| Media extraction | yt-dlp | Metadata and source audio download |
| Transcoding | ffmpeg | MP3 conversion |
| Frontend | HTML, CSS, vanilla JavaScript | Form, playlist UI, polling, and download |
| Reverse proxy | Nginx | Private origin proxy and client-IP restoration |
| Edge proxy | Cloudflare | Public proxy and edge protection |
| Runtime | Docker Compose | App, worker, Redis, and shared temporary volume |
| CI/CD | GitHub Actions and GHCR | Tests, image publication, and SSH deployment |
| Tests | pytest | Security, resource, API, and job-lifecycle checks |

## Important numbers to remember

| Setting | Current value | Reason |
| --- | ---: | --- |
| Gunicorn web workers | 2 | Quick API and download requests |
| RQ conversion workers | 1 | Bound CPU-heavy ffmpeg concurrency |
| Global admitted jobs | 10 | Bound queue and storage pressure |
| `/convert` rate | 5/minute/IP | Limit expensive conversions |
| `/playlist/info` rate | 10/minute/IP | Limit metadata extraction |
| `/downloads` rate | 10/minute/IP | Limit file retrieval abuse |
| Status polling rate | 360/minute/IP | Support multiple jobs polling every two seconds |
| Request body | 2 KB | A URL request should be very small |
| Maximum duration | 90 minutes | Bound download and conversion work |
| Maximum playlist | 50 tracks | Bound metadata response and future work |
| Maximum media/output | 100 MB | Bound individual file usage |
| RQ job timeout | 300 seconds | Stop runaway jobs |
| Result/file retention | 10 minutes | Allow download without permanent storage |
| Shared tmpfs | 512 MB | Hard temporary-storage boundary |

## Project and product questions

### 1. What problem does this application solve?

It lets a user submit a YouTube video or playlist URL and receive an MP3 file.
The engineering problem is managing slow, expensive, failure-prone media work
behind a responsive and abuse-resistant web interface.

### 2. Why did you build this project?

> I wanted a project that involved more than CRUD. It combines external network
> integration, subprocesses, background processing, concurrency control, file
> lifecycle management, security boundaries, a browser UI, and deployment.

### 3. Why did you choose Python?

yt-dlp exposes a mature Python API. Python also integrates cleanly with Flask,
Redis, RQ, filesystem operations, and subprocess-based media tooling.

### 4. Why Flask rather than Django?

The service has a small number of routes and no relational data model, accounts,
or admin interface. Django would add structure that the current product does not
need.

### 5. Why Flask rather than FastAPI?

Flask is sufficient for this small web surface. FastAPI could add stronger type
declarations and generated API documentation, but it would not solve the real
performance issue: yt-dlp and ffmpeg must still run outside request workers.

### 6. Why not build everything in Node.js?

Node could implement the web layer, but Python offers the most direct integration
with yt-dlp. The main architectural decision is separating web and background
work, not the language used for the HTTP layer.

### 7. Is this a monolith or a microservice system?

It is a small distributed application. The app and worker share one code image,
but they run as separate services with Redis between them. I would not call it a
large microservice architecture.

### 8. Why is there no database?

The current jobs and files are temporary, and there are no user accounts or
permanent history. Redis is sufficient. A database becomes necessary for users,
billing, durable job history, ownership, or audit records.

### 9. What is the most technically interesting part?

The change from synchronous conversion to background jobs. It fixed the timeout
at the correct boundary and created a place to apply explicit backpressure.

### 10. What feature would you avoid adding right now?

I would avoid accounts, payments, Kubernetes, and multiple output formats until
there is evidence that users need them. Each adds operational or product scope
without improving the current core flow.

## API and request-flow questions

### 11. What are the application's endpoints?

- `GET /` renders the UI.
- `POST /playlist/info` queues video or playlist metadata extraction.
- `POST /convert` queues an MP3 conversion.
- `GET /jobs/<uuid>` returns job state.
- `GET /downloads/<uuid>` sends a completed MP3.

### 12. Why does the API return `202 Accepted`?

The server accepted the work but has not completed it. Returning 200 with a file
would incorrectly imply synchronous completion.

### 13. What job states can the frontend see?

The public states are `queued`, `running`, `ready`, and `failed`. Internal RQ
states such as stopped and canceled are mapped to a user-facing failure.

### 14. Why does metadata extraction use the queue too?

yt-dlp metadata extraction still performs external network work and can be slow.
Leaving it synchronous would preserve another route capable of exhausting the
Gunicorn worker pool.

### 15. Why use polling rather than WebSockets?

Polling every two seconds is easy to operate through Cloudflare and enough for
jobs measured in seconds or minutes. WebSockets would introduce connection state
without making conversion faster.

### 16. How does the polling timeout work?

The API returns `expires_in`, calculated from worst-case queue wait, execution
timeout, and result retention. JavaScript derives the maximum poll count from
that value.

### 17. Why can the job endpoint return HTTP 200 with `status: failed`?

The status request itself succeeded and returned the terminal state of the job.
An alternative would be a 422 or 500, but treating job failure as state keeps the
polling protocol simple.

### 18. What do 404, 409, and 410 mean on job/download routes?

- 404: no job exists for that UUID.
- 409: the job exists but is not ready for download.
- 410: the job existed, but its temporary output has expired.

### 19. Is `/convert` idempotent?

No. Repeating it creates a new UUID and conversion. Future deduplication could use
a Redis key based on normalized video ID, codec, and quality.

### 20. Could the single-video flow avoid the metadata request?

Yes. Obvious single-video URLs could go directly to `/convert`. The current flow
uses metadata first because it supports playlist preview with one form behavior.

### 21. How are errors presented to users?

Known yt-dlp messages are mapped to stable errors for private, unavailable,
age-restricted, copyrighted, live, or ffmpeg-related content. Unexpected internal
details are logged but not returned to the client.

### 22. Why use UUIDs for jobs?

They prevent filename collisions, do not reveal job order, are hard to guess,
and can be safely constrained by Flask's UUID route converter.

## Concurrency and Cloudflare 524 questions

### 23. What caused the original 524 error?

The original `/convert` route performed network download, ffmpeg conversion, and
file delivery inside a synchronous Gunicorn request. With two workers, later
requests waited without receiving a response and Cloudflare timed out.

### 24. Why did the five-per-minute limiter not prevent it?

Rate limiting is evaluated after Gunicorn assigns the request to a worker. A
request can wait in the server backlog before Flask-Limiter gets a chance to
reject it. Rate and concurrency are different controls.

### 25. Why not only increase Cloudflare or Nginx timeouts?

That preserves the blocking architecture and moves the failure point. It also
keeps workers occupied and does not bound CPU, memory, or storage.

### 26. Why not add more Gunicorn workers?

Each worker could start more expensive media processing. On a small host, more
ffmpeg processes may increase memory pressure and make all jobs slower.

### 27. Why not use threads or gevent?

yt-dlp, filesystem operations, and ffmpeg subprocesses include blocking and
CPU-heavy work. Threads may accept more connections, but they do not create the
required resource boundary.

### 28. How does the current design prevent 524 responses?

The proxy only waits for validation and queue insertion. The route returns 202
in milliseconds while the worker continues independently.

### 29. How did you verify the fix?

In Docker, eleven concurrent simulated clients produced ten 202 responses and
one capacity-controlled 503. The slowest response was 0.082 seconds. Six requests
from one IP produced five 202 responses and one 429 in at most 0.029 seconds.

### 30. What happens when the system is overloaded?

The global admission check returns `503 Service Unavailable` with
`Retry-After: 10`. This is deliberate backpressure rather than an unbounded queue
or proxy timeout.

### 31. Does accepting ten jobs mean running ten ffmpeg processes?

No. Ten jobs may be outstanding, but one RQ worker processes them sequentially.

### 32. Why use one conversion worker?

ffmpeg can use the two available CPU cores itself. One worker keeps resource use
predictable. I would add workers only after measuring CPU, memory, disk, and total
throughput.

### 33. What is head-of-line blocking?

A long job at the front of a FIFO queue delays short jobs behind it. At larger
scale, I could use separate short/long queues or inspect duration before choosing
a queue.

### 34. What is backpressure?

Backpressure means the system refuses new expensive work once its safe capacity
is full instead of accepting more work than it can process.

## Redis and RQ questions

### 35. Why choose RQ rather than Celery?

The project already had Redis and only needed a conventional queue, job states,
timeouts, and a worker. RQ covers that with less configuration than Celery.

### 36. What does Redis store?

- RQ queues and job results
- Flask-Limiter counters
- Global capacity reservations

### 37. How is the global capacity cap atomic?

A Redis Lua script removes expired reservations, counts the remaining entries,
and inserts the new job only when capacity is available. Redis executes the
entire script atomically.

### 38. Why use a sorted set for capacity reservations?

Each job's score is its expiry timestamp. Expired reservations can be removed by
score before counting active entries.

### 39. When is a capacity slot released?

- When a metadata job finishes
- When a conversion fails
- After a completed file is downloaded
- During stale-file cleanup
- Through expiry if a process is killed

### 40. Why are queue TTL and result TTL separate?

Queue TTL controls how long an accepted job may wait before execution. Result TTL
controls how long a completed result remains available. Using the ten-minute
result lifetime as the queue lifetime caused later jobs to expire too early, so
the queue TTL now covers the maximum admitted wait.

### 41. How is queue TTL calculated?

```text
MAX_ACTIVE_JOBS * JOB_TIMEOUT_SECONDS
```

With ten jobs and a 300-second timeout, the queue TTL is 3,000 seconds.

### 42. Why is Redis configured with `noeviction`?

Evicting arbitrary keys could silently remove jobs, capacity state, or rate-limit
counters. `noeviction` turns low memory into an explicit failure.

### 43. Is Redis persistent?

No. Persistence is disabled because jobs are temporary. A restart loses queued
work and rate counters. A commercial service would likely require durable queue
state.

### 44. What happens if Redis is unavailable?

Queue and job-status operations return service-unavailable errors where handled.
One remaining improvement is defining an explicit Flask-Limiter failure or
fallback policy because rate-limit storage also depends on Redis.

### 45. Does RQ guarantee exactly-once execution?

No. The project does not claim exactly-once behavior and does not configure job
retries or idempotency. Repeating an API request creates a new job.

### 46. What happens if the worker crashes?

RQ records ordinary failures. Normal exceptions run cleanup code. Hard process
termination may skip `finally`, so stale-file cleanup and expiring Redis slots
provide fallback recovery.

### 47. Could Redis become a bottleneck?

Not at the current scale; media processing dominates. At higher scale, I would
measure Redis latency, memory, queue depth, and operation rate before separating
job and limiter Redis instances.

## yt-dlp and ffmpeg questions

### 48. What does yt-dlp do?

It extracts YouTube metadata, selects the media format, downloads the source,
and invokes the configured ffmpeg postprocessor.

### 49. What does ffmpeg do?

It decodes the downloaded audio and encodes a 192 kbps MP3.

### 50. Why is Node.js installed?

Recent YouTube extraction may need JavaScript execution. The code detects Node
and supplies it as a yt-dlp JavaScript runtime.

### 51. What format is selected?

`bestaudio/best` prefers the best audio-only stream and falls back to the best
available media format.

### 52. How are live streams handled?

The yt-dlp match filter rejects live and upcoming streams because their duration
and resource use are unpredictable.

### 53. How are long videos handled?

Metadata with a duration over 90 minutes raises `MediaLimitError` before the full
conversion is allowed to complete.

### 54. How are oversized downloads handled?

The service combines yt-dlp's maximum file size, a byte-count progress hook, and
a final output file-size check.

### 55. Why check the final MP3 separately?

The downloaded source and encoded MP3 can have different sizes. A source limit
alone cannot guarantee a bounded final file.

### 56. How are playlists bounded?

yt-dlp requests `MAX_PLAYLIST_ITEMS + 1` flat entries. Receiving the extra entry
proves the playlist exceeds the configured limit, so the job returns a clear
failure instead of silently truncating it.

### 57. Why use fixed 192 kbps output?

It keeps the UI and resource model simple while providing predictable quality.
Supporting multiple qualities would affect storage, processing time, and caching.

### 58. What happens when YouTube changes its site?

yt-dlp must be updated. The dependency is pinned for reproducible deployments,
so updates should pass the existing suite and a controlled staging conversion.

### 59. What if YouTube blocks the server IP?

The job fails. Datacenter IP challenges are an external operational risk, so the
service needs failure monitoring and a controlled live conversion check.

## Security questions

### 60. Is the API authenticated?

No. It is an anonymous public service protected by rate limits, a global job cap,
media limits, temporary storage, and proxy-layer controls.

### 61. Why not put an API secret in the frontend?

A browser-delivered secret is visible to every user and bot. It would not provide
meaningful authentication.

### 62. How would you add bot protection?

Add Cloudflare rate-limit rules and, if needed, Turnstile. The Turnstile token
must be verified server-side before reserving a job slot.

### 63. What is SSRF?

Server-side request forgery occurs when an attacker makes a server request an
unintended destination, potentially including private services or cloud metadata
endpoints.

### 64. How does this project prevent SSRF?

It requires HTTPS, allows only known YouTube hostnames, rejects credentials and
custom ports, resolves DNS, and rejects private, loopback, and link-local IPs.
The worker repeats validation before using the URL.

### 65. Why validate in both the route and worker?

The route rejects bad requests quickly. Worker validation protects the sensitive
network sink even if another code path or direct Redis insertion bypasses the
route.

### 66. Is client-side URL validation a security boundary?

No. It only improves feedback. It intentionally remains simple, while the server
performs the authoritative validation.

### 67. Is DNS rebinding still relevant?

There is a theoretical resolution-to-use gap because validation and yt-dlp may
resolve separately. The strict YouTube hostname allowlist limits attacker control.
Outbound network policy would be a stronger infrastructure-level control.

### 68. How is path traversal prevented?

The client never controls a path. Job IDs pass through Flask's UUID converter,
and storage paths are constructed only from server-generated UUIDs.

### 69. How are attachment filenames made safe?

The storage filename remains the UUID. The video title is stripped to word
characters, spaces, and hyphens before it becomes a download name.

### 70. How are oversized request bodies blocked?

Flask sets a 2 KB maximum body size, and Nginx applies the same limit before the
request reaches Python.

### 71. What rate limits exist?

- `/convert`: 5 per minute per IP
- `/playlist/info`: 10 per minute per IP
- `/downloads`: 10 per minute per IP
- Job polling: 36 times the maximum active-job count per minute

### 72. Why does polling have a high limit?

Ten jobs polling every two seconds can produce 300 requests per minute. The
current 360-per-minute limit includes headroom while still bounding abuse.

### 73. How does the app get the real IP behind Cloudflare?

Nginx accepts `CF-Connecting-IP` only from published Cloudflare networks, then
passes the restored address through the one trusted proxy hop expected by
`ProxyFix`.

### 74. Why restrict direct origin access?

Direct clients could bypass Cloudflare's WAF and edge rate limits. Ports 80 and
443 should accept Cloudflare networks, while ports 3000 and 6379 remain private.

### 75. What security headers exist?

- Content Security Policy
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- Referrer Policy
- `frame-ancestors 'none'`

`X-XSS-Protection` is also present, though CSP is the more relevant modern
control.

### 76. What CSP limitation remains?

The page permits external font and icon CDNs. Self-hosting them or adding
Subresource Integrity would reduce supply-chain exposure.

### 77. Is CSRF a major risk here?

Traditional CSRF is less relevant because there are no authenticated sessions or
user-specific state. Cross-site resource abuse still matters and is addressed by
rate limits, capacity limits, same-origin browser behavior, and Cloudflare.

### 78. Can one user access another user's job?

There is no ownership model. The UUID is effectively a bearer token: difficult to
guess, but usable by anyone who obtains it. Accounts would require authorization
checks on both status and download routes.

### 79. What data is retained?

Redis temporarily stores URLs and job results. The shared volume temporarily
stores MP3 files. RQ logs may contain job arguments. There is no permanent user
database.

### 80. What legal concerns should you mention?

Media downloading may be restricted by copyright, licenses, or platform terms.
A production service should support authorized content, publish clear terms,
handle removal requests, and seek legal advice instead of assuming personal use
always makes downloading lawful.

## Docker, Nginx, Cloudflare, and deployment questions

### 81. Why separate the app and worker containers?

They have different responsibilities and resource profiles. Gunicorn should
answer HTTP quickly; the worker can spend minutes on media processing.

### 82. Why use the same image for both services?

It avoids maintaining two nearly identical images. Compose changes the command
so one container runs Gunicorn and the other runs RQ.

### 83. What health-check issue did this cause?

The image's default HTTP health check was also applied to the RQ worker. The
worker had no port 3000 server, so Docker marked it unhealthy despite successful
job processing.

### 84. How was the health-check issue fixed?

The worker service disables the inherited HTTP health check in Compose. RQ is PID
1, so a worker exit stops the container, and `restart: unless-stopped` restarts
it. The Flask service retains its HTTP health check.

### 85. Would a worker-aware health check be better?

At greater operational maturity, yes. It could verify a recent RQ heartbeat and
Redis registration. The current minimal fix removes a false check without
pretending an HTTP probe measures worker health.

### 86. Why run containers as a non-root user?

It limits the impact of application or dependency compromise inside the
container.

### 87. What resource limits exist?

- App: 1 CPU and 384 MB memory
- Worker: 2 CPUs and 768 MB memory
- Redis: 96 MB memory
- PID limits on app and worker
- Shared 512 MB tmpfs for downloads

### 88. Where are converted files stored?

They are stored as `/app/downloads/<uuid>.mp3` in the shared
`yt-convert_downloads` tmpfs volume.

### 89. Why use tmpfs?

It is temporary, fast, and bounded. It avoids treating converted files as
permanent host data.

### 90. When are files deleted?

They are removed after download, after a failed job, or when stale cleanup finds
them older than ten minutes.

### 91. What does Nginx do?

It proxies traffic to the private Gunicorn port, passes the correct host and
scheme, restores Cloudflare visitor addresses, limits request bodies, and applies
origin timeouts.

### 92. Why bind port 3000 to localhost?

Only local Nginx should reach Gunicorn. Public access would bypass Nginx and
Cloudflare protections.

### 93. How does CI work?

It installs dependencies, runs pytest, builds and starts Docker Compose, waits for
readiness, and checks the homepage.

### 94. How does deployment work?

On a push to `main`, GitHub Actions publishes the image to GHCR, obtains its
immutable digest, copies Compose configuration to the server, and recreates the
services over SSH.

### 95. Why deploy by digest?

A digest identifies exact immutable image contents. It prevents a mutable tag
from silently changing what a deployment installs.

### 96. How does rollback work?

An operator selects a previous successful image digest, updates `APP_IMAGE`,
pulls it, and recreates the Compose services.

### 97. Does deployment roll back automatically?

No. A failed deployment check stops the workflow but does not automatically
restore an earlier version. That is a known operational improvement.

### 98. Why does the worker expose port 3000 in `docker compose ps`?

The image declares `EXPOSE 3000`, so metadata may show the port even though the
worker does not publish it and does not listen on it.

## Testing and reliability questions

### 99. What does the test suite cover?

- Security headers
- URL and JSON validation
- SSRF controls
- Request size
- Media duration and size
- Final MP3 size
- Stale cleanup
- Queue admission and TTL
- Job status and downloads
- Worker-side validation
- Playlist caps
- Rate limits

### 100. How many tests currently pass?

Thirty-seven.

### 101. Why mock yt-dlp?

Live YouTube behavior is slow, unstable, geographically variable, and unsuitable
for deterministic CI. Mocks test application behavior without external failures.

### 102. How do tests simulate a successful conversion?

A fake yt-dlp object writes a small MP3 into pytest's temporary directory and
returns controlled metadata. The test then checks job results, file download, and
cleanup.

### 103. What did integration testing catch?

It caught the worker's inherited HTTP health check, which unit tests could not
observe. It also verified shared storage, Redis policy, real RQ execution, and
fast concurrent admission.

### 104. How was global capacity tested?

Eleven simultaneous requests with distinct simulated client IPs produced ten
accepted jobs and one fast 503.

### 105. How was per-IP limiting tested?

Six simultaneous requests from one simulated IP produced five accepted jobs and
one 429 response.

### 106. What tests are still missing?

- Browser end-to-end polling and download
- Worker crash and timeout recovery
- Redis restart behavior
- Nginx syntax validation in CI
- A controlled authorized staging conversion
- Slow-client download behavior
- Multi-host storage behavior

### 107. What observability exists?

The services emit Gunicorn, application, RQ, and Redis logs. Structured metrics,
distributed tracing, and alerting are not yet implemented.

### 108. What metrics would you add first?

- Queue depth and oldest-job age
- Job duration and failure rate
- Worker heartbeat
- 202, 429, 503, and 524 counts
- Redis memory and latency
- tmpfs utilization
- ffmpeg CPU and memory
- Endpoint P50, P95, and P99 latency
- Download completion and abandonment

### 109. What happens when the browser closes?

The worker continues. If the file is not downloaded, stale cleanup and Redis
expiry eventually remove it and release capacity.

### 110. What happens when the output expires before download?

The download endpoint returns 410 and releases the remaining slot.

### 111. How would you load-test safely?

Separate API admission tests from real media processing. Use fake jobs for large
request bursts, then run a small controlled number of authorized real conversions
while measuring CPU, memory, storage, and duration.

## Frontend and accessibility questions

### 112. Why use vanilla JavaScript?

The UI has limited state and a few interactions. A framework would add build
tooling and dependencies without much current value.

### 113. How are playlist button events handled?

The code uses event delegation on the playlist container rather than adding one
listener per track.

### 114. How is XSS reduced in playlist rendering?

The playlist heading uses `textContent`, and track titles inserted into generated
HTML pass through an escaping function.

### 115. What accessibility features exist?

- A label associated with the URL input
- Semantic navigation, main, section, and footer elements
- ARIA labels for controls and regions
- `aria-live` status messages
- Decorative icons hidden from assistive technology
- Real button disabled/loading states

### 116. How does the frontend download the MP3?

It fetches the completed file, creates a browser Blob URL, clicks a temporary
anchor, then revokes the URL.

### 117. What is the downside of using a Blob?

The entire MP3 is loaded into browser memory before saving. A direct navigation,
streaming response, or signed object-storage URL would handle large files more
efficiently.

### 118. How does polling handle a 429?

It treats 429 as temporary and continues polling instead of abandoning a job that
is still running.

### 119. Can several playlist tracks be submitted together?

Yes. Each track has independent UI state. Server-side rate and capacity limits
remain authoritative.

## Scaling and system-design questions

### 120. What would you improve first?

> The worker health-check issue is fixed. My next priorities would be queue and
> worker metrics, a browser end-to-end test, and replacing Blob downloads with a
> direct streaming or object-storage flow.

### 121. What would change for 1,000 users?

- Private object storage instead of local tmpfs
- Multiple worker hosts
- Autoscaling from queue depth
- Separate metadata and conversion queues
- Authentication and per-user quotas
- Durable job history
- Cloudflare WAF and Turnstile
- Centralized logs, metrics, and alerts
- Signed download URLs
- Deduplication or caching

### 122. Why can the current storage not scale across hosts?

The tmpfs volume belongs to one Docker host. A web container on another machine
cannot see the worker's file. Object storage removes that host affinity.

### 123. How would you add workers safely?

Measure current CPU, memory, storage, and throughput first. Increase workers only
when the host has capacity, then compare total throughput and tail latency.

### 124. Would Kubernetes help now?

No. Compose is sufficient for one host. Kubernetes becomes useful with multiple
hosts, autoscaling, rolling deployments, and stronger orchestration needs.

### 125. How would you add multiple output formats?

Accept a small validated format enum and map it to fixed server-side yt-dlp and
ffmpeg settings. Never pass user-controlled ffmpeg arguments to a subprocess.

### 126. How would you cache results?

Use normalized video ID, codec, and quality as the cache key. Store the file in
object storage and metadata in Redis or a database. Retention must consider legal
and privacy requirements.

### 127. How would you prevent duplicate work?

Atomically create or join an active job keyed by normalized video ID and output
settings instead of creating a new UUID for every request.

### 128. How would you prioritize jobs?

Use separate RQ queues for metadata, short conversions, and long conversions,
then assign worker priority explicitly.

### 129. How would you add user accounts?

Add database-backed identities, associate every job with a user, authorize status
and download routes, and replace or supplement IP limits with per-user quotas.

### 130. How would you make downloads durable?

Upload outputs to private object storage, store job metadata in a database, and
return short-lived signed URLs. Use lifecycle rules to delete expired files.

### 131. What is the largest current architectural limitation?

Completed files depend on a shared single-host volume. The app and worker can be
separated into processes, but not cleanly distributed across machines.

## Behavioral questions

### 132. Tell me about a difficult bug you solved.

> The deployed app returned Cloudflare 524 errors during bursts. I traced the
> request path and found that two synchronous Gunicorn workers were occupied by
> yt-dlp and ffmpeg. The existing rate limit could not help because requests
> waited before Flask processed them. I moved metadata extraction and conversion
> into Redis/RQ jobs, returned 202 responses, added polling and an atomic global
> queue cap, and verified concurrent Docker requests. The slowest admission
> response was 82 milliseconds.

### 133. Tell me about a second bug discovered during verification.

> Unit and Redis integration tests passed, but Docker readiness failed because
> the RQ worker inherited the web image's HTTP health check. The worker processed
> jobs correctly but was still marked unhealthy. I overrode that check for the
> worker and reran Compose readiness successfully.

### 134. Tell me about a tradeoff you made.

> I chose one conversion worker rather than maximizing concurrency. On a
> two-core server, several ffmpeg processes could reduce overall throughput and
> exhaust memory. A queue provides predictable backpressure, and I would only
> increase workers after measurement.

### 135. Tell me about a security decision.

> I treated URLs as untrusted at both the HTTP and worker boundaries. I
> restricted scheme, host, credentials, and port; rejected private resolved
> addresses; used UUID filenames; and never passed a user-controlled path or
> arbitrary ffmpeg arguments to the system.

### 136. What are you most proud of?

> I fixed the timeout by changing the architecture rather than hiding it with a
> larger timeout. The resulting queue also gave the application a clean place to
> enforce capacity and protect the server.

### 137. What would you do differently if starting again?

> I would design slow work as jobs from the beginning, define service-specific
> health checks before deployment, and add queue and conversion metrics before
> production testing.

### 138. What did you learn?

> Rate limits and concurrency limits solve different problems. A five-per-minute
> limit still permits five expensive requests at once, and a reverse proxy can
> time out before application middleware evaluates waiting requests.

### 139. How do you respond when a test exposes a design mistake?

> I first determine whether the test or the implementation has the wrong model.
> In the worker-health case, the check measured HTTP availability on a process
> that was not an HTTP server. I kept the valid web check and removed only the
> incorrect worker inheritance.

### 140. How do you decide whether to add a dependency?

> I prefer existing project tools or standard platform features. For the queue,
> implementing retries, job state, timeouts, and worker lifecycle manually would
> be riskier than adding the focused RQ dependency. I avoided a larger framework
> such as Celery because RQ met the actual requirement.

## Honest limitations and future improvements

Mentioning limitations demonstrates engineering judgment. Do not pretend the
project is a finished global platform.

### Current strengths

- Slow work is outside web requests.
- Overload receives fast and explicit responses.
- Capacity enforcement is atomic across web workers.
- Validation occurs at both HTTP and worker boundaries.
- Media, request, queue, process, and storage resources are bounded.
- Temporary outputs use UUIDs and are cleaned automatically.
- Real client IP handling is designed for Cloudflare and one Nginx hop.
- Tests cover the most important security and lifecycle behavior.
- Docker Compose readiness now succeeds for app, worker, and Redis.

### Current limitations

- One worker creates FIFO head-of-line blocking.
- Redis has no persistence, so restarts lose jobs.
- There is no authentication or per-user ownership.
- UUIDs act as bearer tokens for status and download access.
- Local tmpfs prevents multi-host scaling.
- Browser Blob downloads consume memory proportional to file size.
- No structured metrics, tracing, dashboards, or alerts exist.
- No automated browser end-to-end test exists.
- No automatic deployment rollback exists.
- External CDN assets remain in the CSP supply chain.
- Live YouTube access can fail because of extractor changes or IP challenges.
- Legal and platform-policy requirements need explicit product treatment.

### Recommended improvement order

1. Add a worker-aware readiness/heartbeat check if operations need more than PID
   supervision.
2. Add queue depth, job duration, failure, and storage metrics.
3. Add a browser end-to-end test for polling and download.
4. Stream downloads directly instead of buffering a Blob.
5. Add Cloudflare rate rules and Turnstile if abuse is observed.
6. Add Redis failure policy and recovery tests.
7. Move files to object storage when horizontal scaling is needed.
8. Add accounts, ownership, and quotas only if the product needs them.

## Mock interview

### Interviewer: Give me an overview of the architecture.

Candidate:

> The public request passes through Cloudflare and Nginx to two Gunicorn Flask
> workers. Flask validates the request, applies Redis-backed per-IP limits, and
> atomically reserves one of ten global job slots. It puts the work into RQ and
> returns 202. A separate worker runs yt-dlp and ffmpeg and writes a UUID-named
> MP3 to a shared bounded tmpfs volume. The browser polls Redis-backed job state,
> downloads the file, and the server cleans it up.

### Interviewer: Why not perform the work directly in Flask?

Candidate:

> The operation can take minutes and includes blocking network and CPU-heavy
> work. A synchronous worker cannot serve another request during that time. The
> original design exhausted two workers and caused Cloudflare 524 responses. A
> queue keeps HTTP latency short and bounds expensive concurrency.

### Interviewer: Your API already had rate limiting. Why was that insufficient?

Candidate:

> A rate limit controls requests over time but allows a burst. It also runs only
> after Flask sees the request. When Gunicorn workers are busy, later connections
> may wait before Flask-Limiter executes. I therefore use both per-IP rate limits
> and an atomic global capacity limit.

### Interviewer: How do you know your fix works?

Candidate:

> Unit tests cover validation, queueing, media limits, polling, downloads, and
> cleanup. A real Redis/RQ lifecycle test proved that the Linux worker consumes
> jobs. Docker burst testing showed that ten clients receive 202 and the eleventh
> receives a 503 in under 82 milliseconds. Per-IP testing produced five 202s and
> one 429. Compose readiness also passes after correcting the worker health
> configuration.

### Interviewer: What happens if a user submits a malicious URL?

Candidate:

> The route requires HTTPS and an exact YouTube hostname, rejects credentials
> and nonstandard ports, and rejects private resolved addresses. The RQ worker
> repeats validation before the URL reaches yt-dlp. Invalid requests receive a
> 400 or a failed job state and never reach media download.

### Interviewer: What would fail first at ten times the traffic?

Candidate:

> The one-worker queue would develop wait time before the web layer became CPU
> bound. The admission cap would then return 503. I would monitor queue age and
> conversion duration, move files to object storage, and add workers only after
> confirming CPU and memory capacity.

### Interviewer: What is one decision you would reconsider?

Candidate:

> The frontend always performs metadata extraction before converting a single
> video. That makes playlist behavior simple but adds an external request. I
> would measure how often users submit playlists and potentially send obvious
> video URLs directly to conversion.

## Final rehearsal checklist

Before the interview, make sure you can do the following without reading this
document:

- Deliver the 30-second introduction.
- Draw the complete request and job flow.
- Explain why two synchronous Gunicorn workers caused 524 errors.
- Explain why rate limiting did not solve concurrency.
- Explain why 202 and polling are appropriate.
- Describe the Redis Lua admission algorithm.
- Explain when capacity slots and files are released.
- State the rate, duration, file, playlist, queue, and storage limits.
- Explain SSRF protection and why the worker validates again.
- Explain Cloudflare real-IP restoration and origin firewalling.
- Explain why Redis uses `noeviction` and no persistence.
- Explain why one RQ worker is intentional.
- Describe the Docker worker health-check bug and its fix.
- Explain what the 37 tests cover.
- State the measured concurrent-response results.
- Name at least three honest limitations.
- Explain how object storage enables multi-host scaling.
- Give one difficult-bug story and one tradeoff story.
- Discuss copyright and platform-policy concerns responsibly.

Use this structure for any answer:

> The problem was **X**. I chose **Y** because **Z**. The tradeoff is **A**. I
> verified it using **B**.

