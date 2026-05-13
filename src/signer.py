#!/usr/bin/env python3
"""
Gabagool Bot - Order Signer Module

Purpose:
    EIP-712 order signing for Polymarket CLOB API.
    Signs orders and authentication messages using the EOA private key.

Author: AI-Generated (extracted from discountry/polymarket-trading-bot)
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Extracted from: samples/discountry-base/src/signer.py

Dependencies:
    - eth-account
    - eth-utils

Usage:
    from src.signer import OrderSigner, Order

    signer = OrderSigner(private_key)
    signed = signer.sign_order(order)

Notes:
    - Uses EIP-712 typed data signing
    - Signature type 2 = Gnosis Safe
    - USDC has 6 decimal places
"""

import time
import random
from typing import Optional, Dict, Any
from dataclasses import dataclass
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address


# USDC has 6 decimal places
USDC_DECIMALS = 6


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
        nonce: Unique order nonce (usually timestamp)
        fee_rate_bps: Fee rate in basis points (usually 0)
        signature_type: Signature type (2 = Gnosis Safe)
    """
    token_id: str
    price: float
    size: float
    side: str
    maker: str
    nonce: Optional[int] = None
    fee_rate_bps: int = 0
    signature_type: int = 2

    def __post_init__(self):
        """Validate and normalize order parameters."""
        self.side = self.side.upper()
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {self.side}")

        if not 0 < self.price <= 1:
            raise ValueError(f"Invalid price: {self.price}")

        if self.size <= 0:
            raise ValueError(f"Invalid size: {self.size}")

        if self.nonce is None:
            self.nonce = int(time.time())

        # Convert to integers for blockchain
        self.maker_amount = str(int(self.size * self.price * 10**USDC_DECIMALS))
        self.taker_amount = str(int(self.size * 10**USDC_DECIMALS))
        self.side_value = 0 if self.side == "BUY" else 1


class SignerError(Exception):
    """Base exception for signer operations."""
    pass


class OrderSigner:
    """
    Signs Polymarket orders using EIP-712.

    This signer handles:
    - Authentication messages (L1)
    - Order messages (for CLOB submission)

    Attributes:
        wallet: The Ethereum wallet instance
        address: The signer's address
        domain: EIP-712 domain separator
    """

    # EIP-712 domain for L1 API authentication headers (unchanged in V2)
    AUTH_DOMAIN = {
        "name": "ClobAuthDomain",
        "version": "1",
        "chainId": 137,  # Polygon mainnet
    }

    # EIP-712 domain for ORDER signing (Polymarket CLOB V2 — launched April 28 2026)
    # Source: https://docs.polymarket.com/resources/contracts
    ORDER_DOMAIN = {
        "name": "Polymarket CTF Exchange",
        "version": "2",
        "chainId": 137,
        "verifyingContract": "0xE111180000d2663C0091e4f400237545B87B996B",
    }

    # Neg-Risk markets use a separate contract
    ORDER_DOMAIN_NEG_RISK = {
        "name": "Polymarket CTF Exchange",
        "version": "2",
        "chainId": 137,
        "verifyingContract": "0xe2222d279d744050d28e00520010520000310F59",
    }

    # Order struct for V2 — taker/nonce/feeRateBps/expiration REMOVED,
    # timestamp/metadata/builder ADDED
    # Source: https://github.com/Polymarket/ctf-exchange-v2
    ORDER_TYPES = {
        "Order": [
            {"name": "salt",          "type": "uint256"},
            {"name": "maker",         "type": "address"},
            {"name": "signer",        "type": "address"},
            {"name": "tokenId",       "type": "uint256"},
            {"name": "makerAmount",   "type": "uint256"},
            {"name": "takerAmount",   "type": "uint256"},
            {"name": "side",          "type": "uint8"},
            {"name": "signatureType", "type": "uint8"},
            {"name": "timestamp",     "type": "uint64"},
            {"name": "metadata",      "type": "bytes32"},
            {"name": "builder",       "type": "bytes32"},
        ]
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
        Sign a Polymarket order.

        Args:
            order: Order instance to sign

        Returns:
            Dictionary containing order and signature

        Raises:
            SignerError: If signing fails
        """
        try:
            # Generate a random salt — must be the same in both the signed struct and payload.
            salt = random.randint(1, 2**128)

            # Timestamp in milliseconds (V2 uses this instead of nonce for uniqueness)
            ts_ms = int(time.time() * 1000)

            # Build the V2 EIP-712 order message.
            # V2 dropped: taker, nonce, feeRateBps, expiration
            # V2 added:   timestamp (ms), metadata (bytes32), builder (bytes32)
            order_message = {
                "salt":          salt,
                "maker":         to_checksum_address(order.maker),
                "signer":        self.address,
                "tokenId":       int(order.token_id),
                "makerAmount":   int(order.maker_amount),
                "takerAmount":   int(order.taker_amount),
                "side":          order.side_value,       # 0=BUY, 1=SELL
                "signatureType": order.signature_type,
                "timestamp":     ts_ms,
                "metadata":      b"\x00" * 32,           # bytes32 zero
                "builder":       b"\x00" * 32,           # bytes32 zero (no builder code)
            }

            # Sign using the V2 ORDER domain (not the auth domain)
            signable = encode_typed_data(
                domain_data=self.ORDER_DOMAIN,
                message_types=self.ORDER_TYPES,
                message_data=order_message
            )

            signed = self.wallet.sign_message(signable)

            # Build the API payload body.
            # The CLOB API expects the raw V2 struct fields with signature inside the order object.
            return {
                "order": {
                    "salt":          str(salt),
                    "maker":         to_checksum_address(order.maker),
                    "signer":        self.address,
                    "tokenId":       order.token_id,          # string
                    "makerAmount":   order.maker_amount,      # string (USDC * 1e6)
                    "takerAmount":   order.taker_amount,      # string (shares * 1e6)
                    "side":          order.side_value,        # int: 0=BUY, 1=SELL
                    "signatureType": order.signature_type,
                    "timestamp":     str(ts_ms),
                    "metadata":      "0x" + (b"\x00" * 32).hex(),
                    "builder":       "0x" + (b"\x00" * 32).hex(),
                    "signature":     "0x" + signed.signature.hex(),
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
        nonce: Optional[int] = None,
        fee_rate_bps: int = 0
    ) -> Dict[str, Any]:
        """
        Sign an order from dictionary parameters.

        Args:
            token_id: Market token ID
            price: Price per share
            size: Number of shares
            side: 'BUY' or 'SELL'
            maker: Maker's wallet address
            nonce: Order nonce (defaults to timestamp)
            fee_rate_bps: Fee rate in basis points

        Returns:
            Dictionary containing order and signature
        """
        order = Order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            maker=maker,
            nonce=nonce,
            fee_rate_bps=fee_rate_bps,
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
