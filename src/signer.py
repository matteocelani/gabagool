#!/usr/bin/env python3
"""
Gabagool Bot - Order Signer Module

Purpose:
    EIP-712 order signing for Polymarket CLOB V2 API.
    Signs orders and authentication messages using the EOA private key.

    This implementation is modeled EXACTLY on the official
    Polymarket py-clob-client-v2 source code:
    https://github.com/Polymarket/py-clob-client-v2

Author: AI-Generated (extracted from discountry/polymarket-trading-bot)
Created: 2026-01-26
Modified: 2026-05-13 — Complete rewrite for CLOB V2 (April 28 2026 migration)

Dependencies:
    - eth-account
    - eth-utils

Usage:
    from src.signer import OrderSigner, Order

    signer = OrderSigner(private_key)
    signed = signer.sign_order(order)

Notes:
    - Uses EIP-712 typed data signing
    - V2 signature type 0 = EOA (was 2=Gnosis Safe in V1)
    - USDC has 6 decimal places
    - timestamp is in MILLISECONDS (time_ns // 1_000_000)
"""

import time
import random
from typing import Optional, Dict, Any
from dataclasses import dataclass
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address


# ─── Constants (matching official py-clob-client-v2/constants.py) ─────────────
USDC_DECIMALS = 6
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
BYTES32_ZERO = "0x0000000000000000000000000000000000000000000000000000000000000000"


# ─── EIP-712 typed data (matching official ctf_exchange_v2_typed_data.py) ─────
CTF_EXCHANGE_V2_DOMAIN_NAME = "Polymarket CTF Exchange"
CTF_EXCHANGE_V2_DOMAIN_VERSION = "2"

# Standard markets V2 exchange contract address
CTF_EXCHANGE_V2_ADDRESS = "0xE111180000d2663C0091e4f400237545B87B996B"
# Neg-Risk markets V2 exchange contract address
CTF_EXCHANGE_V2_NEG_RISK_ADDRESS = "0xe2222d279d744050d28e00520010520000310F59"

# Order struct — matches EXACTLY the official V2 definition.
# CRITICAL: timestamp is uint256 (NOT uint64!)
CTF_EXCHANGE_V2_ORDER_STRUCT = [
    {"name": "salt",          "type": "uint256"},
    {"name": "maker",         "type": "address"},
    {"name": "signer",        "type": "address"},
    {"name": "tokenId",       "type": "uint256"},
    {"name": "makerAmount",   "type": "uint256"},
    {"name": "takerAmount",   "type": "uint256"},
    {"name": "side",          "type": "uint8"},
    {"name": "signatureType", "type": "uint8"},
    {"name": "timestamp",     "type": "uint256"},
    {"name": "metadata",      "type": "bytes32"},
    {"name": "builder",       "type": "bytes32"},
]

EIP712_DOMAIN = [
    {"name": "name",              "type": "string"},
    {"name": "version",           "type": "string"},
    {"name": "chainId",           "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]


# ─── Signature types (matching official signature_type_v2.py) ─────────────────
class SignatureTypeV2:
    """Signature types for V2 CTF Exchange orders."""
    EOA = 0             # ECDSA EIP712 signatures signed by EOAs
    POLY_PROXY = 1      # EIP712 signatures signed by EOAs that own Polymarket Proxy wallets
    POLY_GNOSIS_SAFE = 2  # EIP712 signatures signed by EOAs that own Polymarket Gnosis safes


def _hex_to_bytes32(hex_str: str) -> bytes:
    """Convert a 0x-prefixed hex string to a 32-byte value."""
    return bytes.fromhex(hex_str.replace("0x", "").zfill(64))


def _generate_salt() -> str:
    """Generate a random salt for order uniqueness."""
    return str(random.randint(1, 2**128))


@dataclass
class Order:
    """
    Represents a Polymarket order.

    Attributes:
        token_id: The ERC-1155 token ID for the market outcome
        price: Price per share (0-1, e.g., 0.65 = 65%)
        size: Number of shares
        side: Order side ('BUY' or 'SELL')
        maker: The maker's wallet address (Safe/Proxy)
        signature_type: Signature type (0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE)
    """
    token_id: str
    price: float
    size: float
    side: str
    maker: str
    signature_type: int = SignatureTypeV2.EOA  # Default to EOA (was 2=GnosisSafe — WRONG)

    def __post_init__(self):
        """Validate and normalize order parameters."""
        self.side = self.side.upper()
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {self.side}")

        if not 0 < self.price <= 1:
            raise ValueError(f"Invalid price: {self.price}")

        if self.size <= 0:
            raise ValueError(f"Invalid size: {self.size}")

        # Convert to integers for blockchain (matching official builder.py logic)
        # For BUY:  makerAmount = size * price * 10^6 (USDC you pay)
        #           takerAmount = size * 10^6          (shares you receive)
        # For SELL: makerAmount = size * 10^6          (shares you give)
        #           takerAmount = size * price * 10^6  (USDC you receive)
        if self.side == "BUY":
            self.maker_amount = str(int(self.size * self.price * 10**USDC_DECIMALS))
            self.taker_amount = str(int(self.size * 10**USDC_DECIMALS))
        else:  # SELL
            self.maker_amount = str(int(self.size * 10**USDC_DECIMALS))
            self.taker_amount = str(int(self.size * self.price * 10**USDC_DECIMALS))

        self.side_int = 0 if self.side == "BUY" else 1


class SignerError(Exception):
    """Base exception for signer operations."""
    pass


class OrderSigner:
    """
    Signs Polymarket orders using EIP-712 (V2 exchange).

    This signer handles:
    - Authentication messages (L1) — uses ClobAuthDomain
    - Order signing (for CLOB V2 submission) — uses Polymarket CTF Exchange domain

    Implementation modeled on:
    https://github.com/Polymarket/py-clob-client-v2/blob/main/py_clob_client_v2/order_utils/exchange_order_builder_v2.py
    """

    # EIP-712 domain for L1 API authentication headers (unchanged in V2)
    AUTH_DOMAIN = {
        "name": "ClobAuthDomain",
        "version": "1",
        "chainId": 137,  # Polygon mainnet
    }

    def __init__(self, private_key: str):
        """
        Initialize signer with a private key.

        Args:
            private_key: Private key (with or without 0x prefix)

        Raises:
            ValueError: If private key is invalid
        """
        if private_key.startswith("0x"):
            private_key = private_key[2:]

        try:
            self.wallet = Account.from_key(f"0x{private_key}")
        except Exception as e:
            raise ValueError(f"Invalid private key: {e}")

        self.address = self.wallet.address

    @classmethod
    def from_encrypted(
        cls,
        encrypted_data: dict,
        password: str
    ) -> "OrderSigner":
        """
        Create signer from encrypted private key.

        Args:
            encrypted_data: Encrypted key data
            password: Decryption password

        Returns:
            Configured OrderSigner instance

        Raises:
            InvalidPasswordError: If password is incorrect
        """
        from .crypto import KeyManager, InvalidPasswordError

        manager = KeyManager()
        private_key = manager.decrypt(encrypted_data, password)
        return cls(private_key)

    def sign_auth_message(
        self,
        timestamp: Optional[str] = None,
        nonce: int = 0
    ) -> str:
        """
        Sign an authentication message for L1 authentication.

        This signature is used to create or derive API credentials.

        Args:
            timestamp: Message timestamp (defaults to current time)
            nonce: Message nonce (usually 0)

        Returns:
            Hex-encoded signature
        """
        if timestamp is None:
            timestamp = str(int(time.time()))

        # Auth message types
        auth_types = {
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ]
        }

        message_data = {
            "address": self.address,
            "timestamp": timestamp,
            "nonce": nonce,
            "message": "This message attests that I control the given wallet",
        }

        signable = encode_typed_data(
            domain_data=self.AUTH_DOMAIN,
            message_types=auth_types,
            message_data=message_data
        )

        signed = self.wallet.sign_message(signable)
        return "0x" + signed.signature.hex()

    def sign_order(self, order: Order) -> Dict[str, Any]:
        """
        Sign a Polymarket V2 order.

        Builds the EIP-712 typed data structure EXACTLY matching the official
        py-clob-client-v2 ExchangeOrderBuilderV2.build_order_typed_data() method,
        then signs it and returns the JSON body for POST /order.

        Args:
            order: Order instance to sign

        Returns:
            Dictionary matching official order_to_json_v2() output format:
            {
                "order": { salt, maker, signer, tokenId, makerAmount, takerAmount,
                           side, expiration, signatureType, timestamp, metadata,
                           builder, signature },
                "owner": ...,
                "orderType": "GTC"
            }

        Raises:
            SignerError: If signing fails
        """
        try:
            # Generate a random salt (matches generate_order_salt())
            salt = _generate_salt()

            # Timestamp in milliseconds (matches time.time_ns() // 1_000_000)
            ts_ms = str(time.time_ns() // 1_000_000)

            # Signer address
            signer_addr = self.address

            # ─── Build EIP-712 typed data (EXACTLY matching official code) ─────
            # Source: ExchangeOrderBuilderV2.build_order_typed_data()
            typed_data = {
                "primaryType": "Order",
                "types": {
                    "EIP712Domain": EIP712_DOMAIN,
                    "Order": CTF_EXCHANGE_V2_ORDER_STRUCT,
                },
                "domain": {
                    "name": CTF_EXCHANGE_V2_DOMAIN_NAME,
                    "version": CTF_EXCHANGE_V2_DOMAIN_VERSION,
                    "chainId": 137,
                    "verifyingContract": CTF_EXCHANGE_V2_ADDRESS,
                },
                "message": {
                    "salt":          int(salt),
                    "maker":         to_checksum_address(order.maker),
                    "signer":        signer_addr,
                    "tokenId":       int(order.token_id),
                    "makerAmount":   int(order.maker_amount),
                    "takerAmount":   int(order.taker_amount),
                    "side":          order.side_int,
                    "signatureType": int(order.signature_type),
                    "timestamp":     int(ts_ms),
                    "metadata":      _hex_to_bytes32(BYTES32_ZERO),
                    "builder":       _hex_to_bytes32(BYTES32_ZERO),
                },
            }

            # ─── Sign (EXACTLY matching official code) ────────────────────────
            # Source: ExchangeOrderBuilderV2.build_order_signature()
            encoded = encode_typed_data(full_message=typed_data)
            signed = Account.sign_message(encoded, private_key=self.wallet.key)
            signature = "0x" + signed.signature.hex()

            # ─── Build JSON payload (EXACTLY matching official order_to_json_v2) ──
            # Source: order_data_v2.order_to_json_v2()
            # CRITICAL: side in JSON is a STRING ("BUY"/"SELL"), not an int!
            # CRITICAL: salt in JSON is an INT, not a string!
            side_string = "BUY" if order.side == "BUY" else "SELL"

            return {
                "order": {
                    "salt":          int(salt),
                    "maker":         to_checksum_address(order.maker),
                    "signer":        signer_addr,
                    "tokenId":       order.token_id,          # string
                    "makerAmount":   order.maker_amount,      # string
                    "takerAmount":   order.taker_amount,      # string
                    "side":          side_string,             # STRING: "BUY" or "SELL"
                    "expiration":    "0",                     # string: no expiration
                    "signatureType": int(order.signature_type),
                    "timestamp":     ts_ms,                  # string (ms)
                    "metadata":      BYTES32_ZERO,            # hex string
                    "builder":       BYTES32_ZERO,            # hex string
                    "signature":     signature,
                },
            }

        except Exception as e:
            raise SignerError(f"Failed to sign order: {e}")

    def sign_order_dict(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        maker: str,
    ) -> Dict[str, Any]:
        """
        Sign an order from dictionary parameters.

        Args:
            token_id: Market token ID
            price: Price per share
            size: Number of shares
            side: 'BUY' or 'SELL'
            maker: Maker's wallet address

        Returns:
            Dictionary containing order and signature
        """
        order = Order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            maker=maker,
        )
        return self.sign_order(order)

    def sign_message(self, message: str) -> str:
        """
        Sign a plain text message (for API key derivation).

        Args:
            message: Plain text message to sign

        Returns:
            Hex-encoded signature
        """
        from eth_account.messages import encode_defunct

        signable = encode_defunct(text=message)
        signed = self.wallet.sign_message(signable)
        return "0x" + signed.signature.hex()


# Alias for backwards compatibility
WalletSigner = OrderSigner
