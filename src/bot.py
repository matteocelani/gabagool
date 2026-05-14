#!/usr/bin/env python3
"""
Gabagool Bot - Core Trading Bot

Purpose:
    High-level trading bot that wraps ClobClient and OrderSigner
    to provide a simple interface for order execution, position queries,
    and wallet management.

Author: AI-Generated (adapted from discountry/polymarket-trading-bot)
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Adapted from: samples/discountry-base/src/bot.py

Dependencies:
    - src.client (ClobClient, GammaClient)
    - src.signer (OrderSigner)
    - src.config (Config)

Usage:
    from src.bot import TradingBot
    from src.config import Config

    config = Config.from_env()
    bot = TradingBot(config)
    bot.connect()

    # Place order
    result = bot.place_order(token_id, price=0.45, size=10, side='BUY')

    # Get positions
    positions = bot.get_open_orders()

Notes:
    - This is the primary interface for trading
    - Wraps lower-level client and signer modules
    - Supports dry_run mode for testing
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from pathlib import Path

from .client import ClobClient, GammaClient, ApiCredentials, ApiError
from .signer import OrderSigner, Order

try:
    from py_clob_client_v2 import (
        ClobClient as SdkClobClient,
        ApiCreds as SdkApiCreds,
        OrderArgs,
        OrderType as SdkOrderType,
        PartialCreateOrderOptions,
        Side,
    )
    HAS_SDK_V2 = True
except ImportError:
    HAS_SDK_V2 = False


@dataclass
class BotConfig:
    """
    Configuration for the trading bot.

    Attributes:
        private_key: EOA private key for signing
        safe_address: Polymarket Safe/Proxy wallet address
        clob_api_url: CLOB API endpoint
        gamma_api_url: Gamma API endpoint
        chain_id: Polygon chain ID (137 for mainnet)
        dry_run: If True, simulate trades without executing
        creds_path: Path to store/load API credentials
    """
    private_key: str
    safe_address: str
    clob_api_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137
    signature_type: int = 1
    dry_run: bool = False
    creds_path: str = "data/api_creds.json"


class TradingBot:
    """
    High-level trading bot for Polymarket.

    Provides a simple interface for:
    - Order placement and cancellation
    - Position and order queries
    - Price lookups
    - Market discovery

    Example:
        config = BotConfig(
            private_key="0x...",
            safe_address="0x...",
        )
        bot = TradingBot(config)
        bot.connect()

        # Place a buy order
        result = bot.place_order(
            token_id="abc123",
            price=0.45,
            size=10,
            side="BUY"
        )
    """

    def __init__(self, config: BotConfig):
        """
        Initialize the trading bot.

        Args:
            config: BotConfig with wallet and API settings
        """
        self.config = config
        self.logger = logging.getLogger("TradingBot")

        # Initialize components
        self.signer = OrderSigner(config.private_key)
        self.clob = ClobClient(
            host=config.clob_api_url,
            chain_id=config.chain_id,
            funder=config.safe_address,
        )
        self.gamma = GammaClient(host=config.gamma_api_url)

        self._connected = False
        self.sdk_client: "SdkClobClient | None" = None
        self.logger.info(
            "TradingBot initialized (dry_run=%s, address=%s..., sdk_v2=%s)",
            config.dry_run,
            config.safe_address[:10] if config.safe_address else "none",
            HAS_SDK_V2
        )

    def connect(self) -> bool:
        """
        Connect to Polymarket and authenticate.

        Attempts to load cached API credentials, or derives new ones.
        When py-clob-client-v2 is available, also initializes the SDK client
        for order placement (handles EIP-712 signing internally).

        Returns:
            True if connection successful
        """
        try:
            # Try to load cached credentials
            creds_path = Path(self.config.creds_path)
            if creds_path.exists():
                self.logger.info("Loading cached API credentials...")
                creds = ApiCredentials.load(str(creds_path))
                if creds.is_valid():
                    self.clob.set_api_creds(creds)
                    self._init_sdk_client(creds)
                    self._connected = True
                    self.logger.info("Connected using cached credentials")
                    return True

            # Derive new credentials
            self.logger.info("Deriving new API credentials...")
            creds = self.clob.create_or_derive_api_key(self.signer)

            if creds.is_valid():
                self.clob.set_api_creds(creds)
                self._init_sdk_client(creds)

                # Cache credentials
                creds_path.parent.mkdir(parents=True, exist_ok=True)
                creds.save(str(creds_path))
                self.logger.info("Credentials cached to %s", creds_path)

                self._connected = True
                self.logger.info("Connected successfully")
                return True

            self.logger.error("Failed to derive valid credentials")
            return False

        except Exception as e:
            self.logger.error("Connection failed: %s", e)
            return False

    def _init_sdk_client(self, creds: ApiCredentials) -> None:
        """Initialize the official py-clob-client-v2 SDK client for order placement."""
        if not HAS_SDK_V2:
            self.logger.warning("py-clob-client-v2 not installed — orders will use legacy path")
            return

        try:
            sdk_creds = SdkApiCreds(
                api_key=creds.api_key,
                api_secret=creds.secret,
                api_passphrase=creds.passphrase,
            )
            self.sdk_client = SdkClobClient(
                host=self.config.clob_api_url,
                chain_id=self.config.chain_id,
                key=self.config.private_key,
                creds=sdk_creds,
                signature_type=self.config.signature_type,
                funder=self.config.safe_address,
            )
            self.logger.info("SDK V2 client initialized (sig_type=%d)", self.config.signature_type)
        except Exception as e:
            self.logger.error("Failed to init SDK V2 client: %s", e)
            self.sdk_client = None

    @property
    def is_connected(self) -> bool:
        """Check if bot is connected."""
        return self._connected

    # =========================================================================
    # Order Operations
    # =========================================================================

    def place_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str = "BUY",
        order_type: str = "GTC"
    ) -> Optional[Dict[str, Any]]:
        """
        Place an order on Polymarket using the official SDK.

        Args:
            token_id: The token ID (YES or NO token)
            price: Order price (0-1)
            size: Number of shares
            side: 'BUY' or 'SELL'
            order_type: 'GTC' (Good Till Cancelled), 'FOK' (Fill Or Kill)

        Returns:
            Order response dict with order_id and status, or None if failed
        """
        side = side.upper()

        if self.config.dry_run:
            self.logger.info(
                "DRY RUN: Would place %s order: %s @ $%.3f x %.2f",
                side, token_id[:16], price, size
            )
            return {
                "orderID": "dry_run_" + token_id[:8],
                "status": "SIMULATED",
                "price": price,
                "size": size,
                "side": side,
            }

        if not self.sdk_client:
            raise ApiError("SDK V2 client not initialized — cannot place orders")

        try:
            sdk_side = Side.BUY if side == "BUY" else Side.SELL
            sdk_order_type = getattr(SdkOrderType, order_type, SdkOrderType.GTC)

            # SDK handles EIP-712 signing, HMAC auth, and payload construction
            result = self.sdk_client.create_and_post_order(
                order_args=OrderArgs(
                    token_id=token_id,
                    price=price,
                    side=sdk_side,
                    size=size,
                ),
                options=PartialCreateOrderOptions(),
                order_type=sdk_order_type,
            )

            self.logger.info(
                "Order placed: %s %s @ $%.3f x %.2f -> %s",
                side, token_id[:16], price, size,
                result.get("orderID", "unknown") if isinstance(result, dict) else str(result)
            )

            return result if isinstance(result, dict) else {"raw": result}

        except Exception as e:
            self.logger.error("Order failed: %s", e)
            status_code = getattr(e, 'status_code', 0)
            response_body = getattr(e, 'response_body', str(e))
            raise ApiError(
                str(e),
                status_code=status_code if status_code else 400,
                response_body=response_body if response_body else str(e)
            )

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an existing order.

        Args:
            order_id: The order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if self.config.dry_run:
            self.logger.info("DRY RUN: Would cancel order %s", order_id)
            return True

        try:
            result = self.clob.cancel_order(order_id)
            self.logger.info("Order cancelled: %s", order_id)
            return True
        except ApiError as e:
            self.logger.error("Cancel failed for %s: %s", order_id, e)
            return False

    def cancel_all_orders(self) -> Dict[str, Any]:
        """
        Cancel all open orders.

        Returns:
            Dict with 'canceled' and 'not_canceled' lists
        """
        if self.config.dry_run:
            self.logger.info("DRY RUN: Would cancel all orders")
            return {"canceled": [], "not_canceled": []}

        try:
            result = self.clob.cancel_all_orders()
            canceled = result.get("canceled", [])
            self.logger.info("Cancelled %d orders", len(canceled))
            return result
        except ApiError as e:
            self.logger.error("Cancel all failed: %s", e)
            return {"canceled": [], "not_canceled": [], "error": str(e)}

    # =========================================================================
    # Query Operations
    # =========================================================================

    def get_open_orders(self, market: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open orders.

        Args:
            market: Filter by market/condition ID (optional)

        Returns:
            List of open order dicts
        """
        try:
            return self.clob.get_open_orders(market=market)
        except ApiError as e:
            self.logger.error("Failed to get orders: %s", e)
            return []

    def get_trades(
        self,
        token_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get recent trade history.

        Args:
            token_id: Filter by token (optional)
            limit: Maximum number of trades

        Returns:
            List of trade dicts
        """
        try:
            return self.clob.get_trades(token_id=token_id, limit=limit)
        except ApiError as e:
            self.logger.error("Failed to get trades: %s", e)
            return []

    def get_order_book(self, token_id: str) -> Dict[str, Any]:
        """
        Get order book for a token.

        Args:
            token_id: Market token ID

        Returns:
            Order book with bids and asks
        """
        try:
            return self.clob.get_order_book(token_id)
        except ApiError as e:
            self.logger.error("Failed to get orderbook: %s", e)
            return {"bids": [], "asks": []}

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        """
        Get current price for a token.

        Args:
            token_id: The token ID
            side: 'BUY' or 'SELL'

        Returns:
            Current price (0-1), or 0 on error
        """
        try:
            return self.clob.get_price(token_id, side)
        except ApiError as e:
            self.logger.error("Failed to get price: %s", e)
            return 0.0

    def get_spread(self, token_id: str) -> Dict[str, float]:
        """
        Get bid-ask spread for a token.

        Args:
            token_id: Market token ID

        Returns:
            Dict with bid, ask, spread
        """
        try:
            return self.clob.get_spread(token_id)
        except ApiError as e:
            self.logger.error("Failed to get spread: %s", e)
            return {"bid": 0.0, "ask": 0.0, "spread": 0.0}

    # =========================================================================
    # Market Discovery (via Gamma API)
    # =========================================================================

    def get_markets(
        self,
        active: bool = True,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get list of markets from Gamma API.

        Args:
            active: Include only active markets
            limit: Maximum number of markets

        Returns:
            List of market dicts
        """
        try:
            return self.gamma.get_markets(active=active, limit=limit)
        except ApiError as e:
            self.logger.error("Failed to get markets: %s", e)
            return []

    def get_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """
        Get market details by condition ID.

        Args:
            condition_id: Market condition ID

        Returns:
            Market details or None
        """
        try:
            return self.gamma.get_market(condition_id)
        except ApiError as e:
            self.logger.error("Failed to get market %s: %s", condition_id, e)
            return None

    def search_markets(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search markets by text query.

        Args:
            query: Search query (e.g., "BTC", "15 minute")
            limit: Maximum results

        Returns:
            List of matching markets
        """
        try:
            return self.gamma.search_markets(query, limit=limit)
        except ApiError as e:
            self.logger.error("Market search failed: %s", e)
            return []

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def check_prices(
        self,
        yes_token_id: str,
        no_token_id: str
    ) -> Dict[str, float]:
        """
        Get prices for both YES and NO tokens.

        Args:
            yes_token_id: YES token ID
            no_token_id: NO token ID

        Returns:
            Dict with yes_price, no_price, combined_cost, profit_margin
        """
        yes_price = self.get_price(yes_token_id, "BUY")
        no_price = self.get_price(no_token_id, "BUY")
        combined = yes_price + no_price
        profit = 1.0 - combined if combined < 1.0 else 0.0

        return {
            "yes_price": yes_price,
            "no_price": no_price,
            "combined_cost": combined,
            "profit_margin": profit,
            "is_arbitrage": combined < 1.0,
        }

    def __repr__(self) -> str:
        """String representation."""
        status = "connected" if self._connected else "disconnected"
        mode = "DRY RUN" if self.config.dry_run else "LIVE"
        return f"TradingBot({status}, {mode})"


# Convenience function to create bot from Config
def create_bot_from_config(config: "Config") -> TradingBot:
    """
    Create TradingBot from Config object.

    Args:
        config: Config instance

    Returns:
        Configured TradingBot
    """
    from .config import Config

    private_key = config.get_private_key()
    if not private_key:
        raise ValueError("POLY_PRIVATE_KEY not set in environment")

    bot_config = BotConfig(
        private_key=private_key,
        safe_address=config.safe_address,
        clob_api_url=config.clob.host,
        chain_id=config.clob.chain_id,
        signature_type=config.clob.signature_type,
        dry_run=config.dry_run,
        creds_path=str(Path(config.data_dir) / "api_creds.json"),
    )

    return TradingBot(bot_config)
