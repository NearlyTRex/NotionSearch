# Installing Docker

NotionSearch runs in Docker, so this is the only thing you need to install first.
You do this once.

## Ubuntu, Linux Mint, and Debian

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo ${UBUNTU_CODENAME:-$VERSION_CODENAME}) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Then let your own user run Docker without `sudo`:

```bash
sudo usermod -aG docker $USER
```

**Now log out and log back in.** This step is easy to skip and it is what makes the
next command work.

Check all three pieces are present:

```bash
docker run --rm hello-world
docker compose version
docker buildx version
```

### A note for Linux Mint

Docker publishes no repository for Mint's own release names (`zena`, `xia`, `wilma`
and so on). The repository line has to point at the Ubuntu version underneath —
`noble` for Mint 22.x, `jammy` for Mint 21.x.

The command above handles this automatically: `${UBUNTU_CODENAME:-$VERSION_CODENAME}`
reads the Ubuntu base from `/etc/os-release`. If you ever write the line by hand and
use the Mint codename instead, `apt-get update` will fail with a 404.

## Mac and Windows

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start
it. Compose and Buildx are included, and there is no group step.

## Troubleshooting

**`permission denied while trying to connect to the Docker daemon socket`**

You are not in the `docker` group yet, or you haven't logged out and back in since
being added. Confirm you're in the group:

```bash
getent group docker
```

If your username is listed but the error persists, your current session still has
the old group list. Log out and back in. To test without logging out:

```bash
sg docker -c "docker info"
```

**`Cannot connect to the Docker daemon`**

The service isn't running:

```bash
sudo systemctl start docker
sudo systemctl enable docker   # start it automatically at boot
```

**`404 Not Found` when running `apt-get update`**

The repository line has a Mint codename in it instead of the Ubuntu one. Delete
`/etc/apt/sources.list.d/docker.list` and re-run the block above.

---

Next: [Getting started](../usage/getting-started.md)
