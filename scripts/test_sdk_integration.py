#!/usr/bin/env python3
"""
SDK V2 Integration Test

Validates the full order flow without placing real trades.
Run from project root: python scripts/test_sdk_integration.py
"""

import sys
import os
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load env
from dotenv import load_dotenv
config_dir = PROJECT_ROOT / "config"
load_dotenv(config_dir / ".env")
load_dotenv()

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

failed = False


def check(label: str, condition: bool, detail: str = "") -> bool:
    global failed
    if condition:
        print(f"  {PASS} {label}" + (f"  ({detail})" if detail else ""))
    else:
        print(f"  {FAIL} {label}" + (f"  ({detail})" if detail else ""))
        failed = True
    return condition


# ─── Step 1: SDK Import ───
print("\n[1/6] SDK Import")
try:
    from py_clob_client_v2 import (
        ClobClient as SdkClobClient,
        ApiCreds,
        OrderArgs,
        OrderType,
        PartialCreateOrderOptions,
        Side,
    )
    check("py-clob-client-v2 importable", True)
except ImportError as e:
    check("py-clob-client-v2 importable", False, str(e))
    print(f"\n  {WARN} Install with: pip install py-clob-client-v2>=1.0.0")
    sys.exit(1)

# ─── Step 2: Environment Variables ───
print("\n[2/6] Environment Variables")
pk = os.environ.get("POLY_PRIVATE_KEY", "")
safe_addr = os.environ.get("POLY_SAFE_ADDRESS", "")

check("POLY_PRIVATE_KEY set", bool(pk), f"{len(pk)} chars")
check("POLY_SAFE_ADDRESS set", bool(safe_addr), safe_addr[:12] + "..." if safe_addr else "missing")

if not pk or not safe_addr:
    print(f"\n  {WARN} Set these in config/.env before running")
    sys.exit(1)

# ─── Step 3: Cached Credentials ───
print("\n[3/6] API Credentials")
creds_path = PROJECT_ROOT / "data" / "api_creds.json"
creds = None

if creds_path.exists():
    with open(creds_path) as f:
        data = json.load(f)
    api_key = data.get("apiKey", "")
    secret = data.get("secret", "")
    passphrase = data.get("passphrase", "")
    has_creds = bool(api_key and secret and passphrase)
    check("Cached creds found", has_creds, str(creds_path))

    if has_creds:
        creds = ApiCreds(
            api_key=api_key,
            api_secret=secret,
            api_passphrase=passphrase,
        )
else:
    check("Cached creds found", False, "data/api_creds.json not found")
    print(f"  {WARN} Run the bot once to derive credentials, or derive manually")

# ─── Step 4: SDK Client Init ───
print("\n[4/6] SDK Client Initialization")
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

# Detect signature type from config
sig_type = 1
yaml_path = PROJECT_ROOT / "config" / "default.yaml"
if yaml_path.exists():
    try:
        import yaml
        with open(yaml_path) as f:
            cfg = yaml.safe_load(f)
        sig_type = cfg.get("clob", {}).get("signature_type", 1)
    except Exception:
        pass

try:
    init_kwargs = dict(
        host=CLOB_HOST,
        chain_id=CHAIN_ID,
        key=pk,
        signature_type=sig_type,
        funder=safe_addr,
    )
    if creds:
        init_kwargs["creds"] = creds

    client = SdkClobClient(**init_kwargs)
    check("SdkClobClient created", True, f"sig_type={sig_type}, funder={safe_addr[:12]}...")
except Exception as e:
    check("SdkClobClient created", False, str(e))
    sys.exit(1)

# ─── Step 5: API Connectivity ───
print("\n[5/6] API Connectivity")
try:
    ok = client.get_ok()
    check("GET /ok", True, str(ok))
except Exception as e:
    check("GET /ok", False, str(e))

try:
    server_time = client.get_server_time()
    check("GET /time", True, str(server_time))
except Exception as e:
    check("GET /time", False, str(e))

# Fetch a real token to test with
test_token_id = None
try:
    markets = client.get_markets()
    if isinstance(markets, dict) and "data" in markets:
        for m in markets["data"]:
            tokens = m.get("tokens", [])
            if tokens:
                test_token_id = tokens[0].get("token_id")
                if test_token_id:
                    break

    if test_token_id:
        check("Fetched live market", True, f"token={test_token_id[:20]}...")
    else:
        check("Fetched live market", False, "no tokens found")
except Exception as e:
    check("Fetched live market", False, str(e))

if test_token_id:
    try:
        tick = client.get_tick_size(test_token_id)
        check("GET tick_size", True, f"tick_size={tick}")
    except Exception as e:
        check("GET tick_size", False, str(e))

    try:
        neg = client.get_neg_risk(test_token_id)
        check("GET neg_risk", True, f"neg_risk={neg}")
    except Exception as e:
        check("GET neg_risk", False, str(e))

# ─── Step 6: Order Signing (no post) ───
print("\n[6/6] Order Signing (dry — no real order posted)")
if not test_token_id:
    print(f"  {WARN} Skipped — no test token available.")
else:
    try:
        signed_order = client.create_order(
            order_args=OrderArgs(
                token_id=test_token_id,
                price=0.01,
                side=Side.BUY,
                size=1,
            ),
            options=PartialCreateOrderOptions(),
        )
        has_sig = hasattr(signed_order, "signature") or (
            isinstance(signed_order, dict) and "signature" in signed_order
        )
        check("create_order (sign only)", True, f"type={type(signed_order).__name__}")
        check("Signature present", has_sig)
        print(f"\n  {PASS} Order signing works — the SDK can build valid V2 payloads!")
    except Exception as e:
        check("create_order (sign only)", False, str(e))
        print(f"\n  {FAIL} Signing failed — check signature_type ({sig_type}) and funder address")

# ─── Summary ───
print("\n" + "=" * 56)
if failed:
    print(f"  {FAIL}  Some checks failed. Review errors above.")
    sys.exit(1)
else:
    print(f"  {PASS}  All checks passed! Ready to deploy.")
    sys.exit(0)
