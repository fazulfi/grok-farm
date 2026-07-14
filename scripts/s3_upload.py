#!/usr/bin/env python3
"""S3 helpers for Grok Farm: upload with Content-Length + boto3 retention.

Usage:
  s3_upload.py <local_file> <s3_uri_or_key> [more keys...]
  s3_upload.py --retention [--days N] [--prefix farm-vps/HOST/]

Env: S3_ENDPOINT, S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
Optional: AWS_DEFAULT_REGION, RETENTION_DAYS (default 14), S3_PREFIX,
  HOST_NAME, S3_HOST_ROOT (full host root under bucket; preferred for retention
  when set by s3_backup.sh — avoids farm-vps/grokN/grokN double segment)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def make_client(*, checksum: bool = True):
    kwargs = {
        "endpoint_url": os.environ["S3_ENDPOINT"],
        "aws_access_key_id": os.environ["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": os.environ["AWS_SECRET_ACCESS_KEY"],
        "region_name": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    }
    if checksum:
        kwargs["config"] = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
    else:
        kwargs["config"] = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
    return boto3.client("s3", **kwargs)


def parse_s3(uri_or_key, default_bucket):
    if uri_or_key.startswith("s3://"):
        rest = uri_or_key[5:]
        b, _, k = rest.partition("/")
        return b, k
    return default_bucket, uri_or_key


def upload(local, dest, client=None):
    bucket = os.environ["S3_BUCKET"]
    b, key = parse_s3(dest, bucket)
    size = os.path.getsize(local)
    client = client or make_client()
    print(f"put s3://{b}/{key} ({size} bytes)")
    with open(local, "rb") as f:
        client.put_object(Bucket=b, Key=key, Body=f, ContentLength=size)
    return 0


def list_all_objects(client, bucket: str, prefix: str):
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            yield obj
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")


def retention_cleanup(
    days: int | None = None,
    prefix: str | None = None,
    dry_run: bool = False,
) -> int:
    """Delete dated backup objects older than RETENTION_DAYS; keep latest.* and LATEST.txt."""
    days = int(days if days is not None else os.environ.get("RETENTION_DAYS", "14") or "14")
    if days < 1:
        print("retention skip: RETENTION_DAYS < 1")
        return 0
    bucket = os.environ["S3_BUCKET"]
    s3_prefix = (os.environ.get("S3_PREFIX") or "farm-vps").strip().strip("/")
    host = (os.environ.get("HOST_NAME") or os.environ.get("HOSTNAME") or "").strip()
    host_root = (os.environ.get("S3_HOST_ROOT") or "").strip().strip("/")
    if prefix:
        pref = prefix.lstrip("/")
        if not pref.endswith("/"):
            pref += "/"
    elif host_root:
        pref = f"{host_root}/"
    elif host:
        # Avoid farm-vps/grokN/grokN when S3_PREFIX already ends with host.
        if s3_prefix == host or s3_prefix.endswith(f"/{host}"):
            pref = f"{s3_prefix}/"
        else:
            pref = f"{s3_prefix}/{host}/"
    else:
        pref = f"{s3_prefix}/"
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    client = make_client(checksum=False)
    deleted = 0
    scanned = 0
    for obj in list_all_objects(client, bucket, pref):
        scanned += 1
        key = obj["Key"]
        base = key.rsplit("/", 1)[-1]
        if base == "LATEST.txt" or base.startswith("latest."):
            continue
        # Prefer age/encrypted archives; still clean old plaintext if any remain
        if not (
            base.endswith(".tgz.age")
            or base.endswith(".tgz")
            or base.endswith(".age")
        ):
            continue
        lm = obj.get("LastModified")
        if lm is None:
            continue
        if lm.tzinfo is None:
            lm = lm.replace(tzinfo=dt.timezone.utc)
        if lm >= cutoff:
            continue
        print(f"retention delete s3://{bucket}/{key} last={lm.isoformat()}")
        if not dry_run:
            client.delete_object(Bucket=bucket, Key=key)
        deleted += 1
    print(
        f"retention {'would_delete' if dry_run else 'deleted'} {deleted} "
        f"of {scanned} objects under {pref!r} older than {days}d"
    )
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--retention", "retention"):
        p = argparse.ArgumentParser(prog="s3_upload.py --retention")
        p.add_argument("--days", type=int, default=None)
        p.add_argument("--prefix", default=None)
        p.add_argument("--dry-run", action="store_true")
        args = p.parse_args(argv[1:])
        try:
            return retention_cleanup(days=args.days, prefix=args.prefix, dry_run=args.dry_run)
        except Exception as e:
            print(f"retention error: {e}")
            return 1

    if len(argv) < 2:
        print(
            "Usage:\n"
            "  s3_upload.py <local_file> <s3_uri_or_key> [more keys...]\n"
            "  s3_upload.py --retention [--days N] [--prefix PATH/] [--dry-run]"
        )
        return 1
    local = argv[0]
    for dest in argv[1:]:
        try:
            upload(local, dest)
        except ClientError as e:
            print(f"retry without checksum opts: {e}")
            client = make_client(checksum=False)
            b, key = parse_s3(dest, os.environ["S3_BUCKET"])
            data = open(local, "rb").read()
            client.put_object(Bucket=b, Key=key, Body=data, ContentLength=len(data))
            print(f"put-ok s3://{b}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
