# Ubuntu deployment

The workflow tests the app and Docker Compose startup on pull requests. Pushes to
`main` also publish to GHCR and deploy that exact image digest over SSH. Nginx runs
on the Ubuntu host; Gunicorn and Redis run in Docker. The published image targets
an x86-64 Ubuntu server. ARM servers need a matching image build.

## One-time server setup

1. Install Docker Engine and the Compose plugin using the
   [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
2. Create a deployment user and directory:

   ```sh
   sudo adduser --disabled-password --gecos '' deploy
   sudo usermod -aG docker deploy
   sudo install -d -o deploy -g deploy /opt/yt-convert
   sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
   ```

   Add your CI SSH public key to `/home/deploy/.ssh/authorized_keys`, owned by
   `deploy` with mode `600`. Docker group membership grants root-level access.
   Verify a new SSH login as `deploy` can run `docker info` without sudo.
3. For a private GHCR package, log in **as deploy** once with a GitHub token that
   has `read:packages` and access to the package:

   ```sh
   docker login ghcr.io -u YOUR_GITHUB_USERNAME
   ```

   Enter the token at the password prompt. Public packages do not require login.
4. Install Nginx and copy this repository's `deploy/nginx.conf` to
   `/etc/nginx/sites-available/yt-convert`. Replace `convert.example.com` with
   your Cloudflare-proxied domain, pointing its DNS record to this server. The
   config trusts `CF-Connecting-IP` only from Cloudflare's published address
   ranges so Flask rate limits individual visitors instead of Cloudflare edges.

   ```sh
   sudo apt update
   sudo apt install -y nginx
   sudo cp deploy/nginx.conf /etc/nginx/sites-available/yt-convert
   sudo nano /etc/nginx/sites-available/yt-convert
   sudo ln -s /etc/nginx/sites-available/yt-convert /etc/nginx/sites-enabled/yt-convert
   sudo nginx -t
   sudo systemctl enable --now nginx
   sudo systemctl reload nginx
   ```

   Allow SSH (port 22), HTTP (80), and HTTPS (443) in your server/cloud firewall.
   Ports 3000 and 6379 should not be public. Restrict ports 80 and 443 to
   [Cloudflare's IP ranges](https://www.cloudflare.com/ips/) after certificate
   setup so clients cannot bypass Cloudflare. Recheck those ranges when updating
   Nginx. Nginx will return 502 until the first successful deployment.

## GitHub configuration

Create a `production` environment under repository Settings → Environments and
add these environment secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Ubuntu server hostname or IP |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | Dedicated SSH private key without a passphrase |
| `DEPLOY_KNOWN_HOSTS` | Verified OpenSSH known-hosts entry for the server |

Obtain the known-hosts entry with `ssh-keyscan -H YOUR_SERVER` and compare its
fingerprint with the server's host key through your trusted server console
(`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) before saving it. The workflow
uses SSH's normal strict host verification. SSH must be reachable from the GitHub
runner on port 22.

Push to `main` after setup. The workflow copies `compose.yaml`, pulls the published
image, waits for healthy containers, and checks the local HTTP endpoint. Successful
deployments save the image reference in `/opt/yt-convert/.env`. Nginx configuration
changes are installed manually with `nginx -t` and reload as above.

After DNS and HTTP work, enable HTTPS on Ubuntu:

```sh
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d convert.example.com
sudo certbot renew --dry-run
```

## Verify and operate

```sh
cd /opt/yt-convert
docker compose ps
docker compose logs --tail=100 app worker redis
curl --fail http://127.0.0.1:3000/
curl --fail https://convert.example.com/
```

To roll back, take a previous successful image reference from the Actions logs or
GHCR and run as `deploy`:

```sh
cd /opt/yt-convert
export APP_IMAGE=ghcr.io/OWNER/REPO@sha256:PREVIOUS_DIGEST
docker compose pull
docker compose up --no-build --wait --wait-timeout 120
printf 'APP_IMAGE=%s\n' "$APP_IMAGE" > .env
```

Use lowercase owner/repository names. Deployments briefly interrupt service and
can interrupt active conversions; failed health checks fail the workflow but do
not automatically roll back. The web app returns queued jobs immediately; one RQ
worker performs yt-dlp and ffmpeg work while the browser polls for completion.
The shared downloads volume is a 512 MB temporary filesystem. At most 10 jobs are
admitted, each conversion is limited to 100 MB and 90 minutes, playlists are
limited to 50 tracks, and abandoned files expire after 10 minutes. Redis counters
and queued jobs reset when Redis restarts. A healthy homepage verifies startup,
not the worker's live YouTube access; test a conversion after deployment because
datacenter IPs may be challenged.

Client-IP handling follows Flask's [trusted proxy guidance](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/).
Compose readiness uses [`up --wait`](https://docs.docker.com/reference/cli/docker/compose/up/).
