#!/usr/bin/env python3
"""Upload to S3-compatible storage (cloudhost is3) with explicit Content-Length."""
import os
import sys

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def make_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def parse_s3(uri_or_key, default_bucket):
    if uri_or_key.startswith("s3://"):
        rest = uri_or_key[5:]
        b, _, k = rest.partition("/")
        return b, k
    return default_bucket, uri_or_key


def upload(local, dest):
    bucket = os.environ["S3_BUCKET"]
    b, key = parse_s3(dest, bucket)
    size = os.path.getsize(local)
    client = make_client()
    print(f"put s3://{b}/{key} ({size} bytes)")
    with open(local, "rb") as f:
        client.put_object(Bucket=b, Key=key, Body=f, ContentLength=size)
    return 0


def main():
    if len(sys.argv) < 3:
        print("Usage: s3_upload.py <local_file> <s3_uri_or_key> [more keys...]")
        return 1
    local = sys.argv[1]
    for dest in sys.argv[2:]:
        try:
            upload(local, dest)
        except ClientError as e:
            print(f"retry without checksum opts: {e}")
            client = boto3.client(
                "s3",
                endpoint_url=os.environ["S3_ENDPOINT"],
                aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
            b, key = parse_s3(dest, os.environ["S3_BUCKET"])
            data = open(local, "rb").read()
            client.put_object(Bucket=b, Key=key, Body=data, ContentLength=len(data))
            print(f"put-ok s3://{b}/{key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
