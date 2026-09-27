# Alpine rather than Debian slim: the slim image carries dozens of HIGH-severity
# CVEs in packages Debian marks essential (util-linux, ncurses, perl-base,
# systemd libraries) that this app never uses and that cannot be removed. Alpine
# ships busybox instead, which leaves the image scan clean at half the size.
# Every dependency, including uvicorn[standard]'s native extensions, publishes
# musllinux wheels, so nothing is compiled here.
FROM python:3.14-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NOTIONSEARCH_DATA=/data

WORKDIR /srv

# su-exec drops privileges in the entrypoint after fixing up /data ownership.
# apk upgrade picks up security fixes published since the base image was last
# rebuilt, so a rebuild is always enough to clear a fixable CVE.
RUN apk upgrade --no-cache \
    && apk add --no-cache su-exec

# Dependencies first so code edits don't invalidate the layer.
COPY requirements.txt .
# pip is a build-time tool: nothing at runtime uses it. Leaving it behind ships
# its vendored dependency tree (msgpack, setuptools and friends, listed in
# pip/_vendor/vendor.txt), which vulnerability scanners correctly report against
# the image even though that code never executes here. Removing it deletes the
# vulnerable files rather than suppressing the finding.
# --require-hashes: requirements.txt is hash-locked, so a tampered or swapped
# package fails the build instead of shipping. --no-deps because the lock
# already lists every transitive dependency.
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt \
    && rm -rf /usr/local/lib/python3.*/site-packages/pip \
              /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
              /usr/local/lib/python3.*/ensurepip \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

# pyproject.toml is where the app reads its version from.
COPY pyproject.toml ./
COPY app/ ./app/
COPY web/ ./web/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
