#!/usr/bin/env python3
"""
Gabagool Bot - Core Arbitrage Strategy

Purpose:
    The gabagool arbitrage strategy - buy YES and NO when both
    are temporarily cheap. If combined cost < $1.00, profit is
    guaranteed regardless of outcome.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Dependencies:
    - src.bot
    - src.position_tracker
    - src.risk_manager
    - src.stats_tracker

Usage:
    from strategies.gabagool_strategy import GabagoolStrategy

    strategy = GabagoolStrategy(
        bot, config, position_tracker, risk_manager, stats_tracker, db
    )
    await strategy.scan_opportunities()

Notes:
    - Scans 15-minute BTC/ETH/SOL markets
    - Executes when YES + NO combined cost < threshold
    - Manages position pairing automatically
    - Bot methods are synchronous, wrapped with asyncio.to_thread()
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from src.gamma_client import GammaClient, Market


@dataclass
class StrategyConfig:
    """Configuration for gabagool strategy."""
    # Entry thresholds
    yes_threshold: float = 0.48      # Buy YES if below this
    no_threshold: float = 0.48       # Buy NO if below this
    max_combined_cost: float = 0.97  # Max total cost for pair
    min_profit_margin: float = 0.02  # Min profit to enter

    # Trade sizing
    trade_size: float = 5.0          # USD per side

    # Target assets
    assets: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])


class GabagoolStrategy:
    """
    Gabagool arbitrage strategy.

    Monitors 15-minute markets for arbitrage opportunities.
    Executes when both YES and NO can be bought cheaply enough
    that combined cost < $1.00, guaranteeing profit.

    The TradingBot uses synchronous methods, so this strategy
    wraps them with asyncio.to_thread() for async compatibility.
    """

    def __init__(
        self,
        bot: Any,
        config: Dict[str, Any],
        position_tracker: Any,
        risk_manager: Any,
        stats_tracker: Any,
        db: Any
    ):
        """
        Initialize gabagool strategy.

        Args:
            bot: TradingBot instance (synchronous methods)
            config: Strategy configuration dict
            position_tracker: PositionTracker instance
            risk_manager: RiskManager instance
            stats_tracker: StatsTracker instance
            db: TradingDatabase instance
        """
        self.bot = bot
        self.position_tracker = position_tracker
        self.risk_manager = risk_manager
        self.stats_tracker = stats_tracker
        self.db = db
        self.logger = logging.getLogger("gabagool_strategy")

        # Parse config
        self.config = StrategyConfig(
            yes_threshold=config.get("yes_threshold", 0.48),
            no_threshold=config.get("no_threshold", 0.48),
            max_combined_cost=config.get("max_combined_cost", 0.97),
            min_profit_margin=config.get("min_profit_margin", 0.02),
            trade_size=config.get("trade_size", 5.0),
            assets=config.get("assets", ["BTC", "ETH", "SOL"])
        )

        self.logger.info(
            "GabagoolStrategy initialized | Thresholds: YES=%.2f NO=%.2f | Size: $%.2f",
            self.config.yes_threshold,
            self.config.no_threshold,
            self.config.trade_size
        )

    async def scan_opportunities(self) -> int:
        """
        Scan for arbitrage opportunities in target markets.

        Returns:
            Number of arbitrages executed
        """
        executed = 0

        # Get 15-minute markets from Gamma API via TradingBot
        markets = await self._discover_markets()

        if not markets:
            self.logger.debug("No target markets found")
            return 0

        self.logger.debug("Scanning %d markets for arbitrage", len(markets))

        for market in markets:
            try:
                # Get current prices (wrap sync calls in to_thread)
                yes_price = await asyncio.to_thread(
                    self.bot.get_price, market.yes_token_id, "BUY"
                )
                no_price = await asyncio.to_thread(
                    self.bot.get_price, market.no_token_id, "BUY"
                )

                # Skip if prices unavailable
                if yes_price <= 0 or no_price <= 0:
                    continue

                # Check for arbitrage opportunity
                if self._is_opportunity(yes_price, no_price):
                    self.logger.info(
                        "Found opportunity: %s | YES: $%.4f, NO: $%.4f | Combined: $%.4f",
                        market.id[:16], yes_price, no_price, yes_price + no_price
                    )
                    success = await self.execute_arbitrage(
                        market, yes_price, no_price
                    )
                    if success:
                        executed += 1

            except Exception as e:
                self.logger.error(
                    "Error scanning market %s: %s",
                    getattr(market, 'id', 'unknown')[:16], e
                )

        return executed

    async def _discover_markets(self) -> List[Market]:
        """
        Discover target 15-minute markets via Gamma API.

        Returns:
            List of Market objects for scanning
        """
        try:
            # Use GammaClient to discover active 15-minute markets
            gamma_client = GammaClient()
            markets = await asyncio.to_thread(
                gamma_client.get_all_active_markets, self.config.assets
            )
            self.logger.debug("Discovered %d active markets", len(markets))
            return markets

        except Exception as e:
            self.logger.warning("Market discovery failed: %s", e)
            return []

    def _is_opportunity(self, yes_price: float, no_price: float) -> bool:
        """
        Check if prices represent an arbitrage opportunity.

        Args:
            yes_price: Current YES price
            no_price: Current NO price

        Returns:
            True if opportunity exists
        """
        # Both prices must be below thresholds
        if yes_price >= self.config.yes_threshold:
            return False
        if no_price >= self.config.no_threshold:
            return False

        # Combined cost must leave profit margin
        combined_cost = yes_price + no_price
        if combined_cost >= self.config.max_combined_cost:
            return False

        # Profit margin check
        profit_margin = 1.0 - combined_cost
        if profit_margin < self.config.min_profit_margin:
            return False

        return True

    async def execute_arbitrage(
        self,
        market: Market,
        yes_price: float,
        no_price: float
    ) -> bool:
        """
        Execute arbitrage trade on both sides.

        Args:
            market: MarketInfo with token IDs
            yes_price: Current YES price
            no_price: Current NO price

        Returns:
            True if arbitrage executed successfully
        """
        market_id = market.id

        # Risk validation
        is_valid, reason = self.risk_manager.validate_arbitrage(
            market_id,
            yes_price,
            no_price,
            self.config.trade_size,
            self.position_tracker.active_positions
        )

        if not is_valid:
            self.logger.warning(
                "Risk validation failed for %s: %s",
                market_id[:16], reason
            )
            return False

        # Calculate position sizes
        yes_shares = self.config.trade_size / yes_price
        no_shares = self.config.trade_size / no_price

        self.logger.info(
            "Executing arbitrage: %s | YES: %.2f @ $%.4f | NO: %.2f @ $%.4f",
            market_id[:16], yes_shares, yes_price, no_shares, no_price
        )

        try:
            # Place YES order (wrap sync method)
            yes_order = await asyncio.to_thread(
                self.bot.place_order,
                market.yes_token_id,
                yes_price,
                yes_shares,
                "BUY"
            )

            if not yes_order:
                self.logger.error("Failed to place YES order")
                return False

            self.logger.info("YES order placed: %s", yes_order.get("orderID", ""))

            # Small delay between orders
            await asyncio.sleep(0.5)

            # Place NO order (wrap sync method)
            no_order = await asyncio.to_thread(
                self.bot.place_order,
                market.no_token_id,
                no_price,
                no_shares,
                "BUY"
            )

            if not no_order:
                self.logger.error("Failed to place NO order - YES order still active!")
                # TODO: Consider canceling YES order or handling partial fill
                return False

            self.logger.info("NO order placed: %s", no_order.get("orderID", ""))

            # Update position tracker
            self.position_tracker.add_yes_position(
                market_id=market_id,
                shares=yes_shares,
                cost=self.config.trade_size,
                yes_token_id=market.yes_token_id,
                no_token_id=market.no_token_id
            )

            self.position_tracker.add_no_position(
                market_id=market_id,
                shares=no_shares,
                cost=self.config.trade_size
            )

            # Record in stats
            profit_margin = 1.0 - (yes_price + no_price)
            self.stats_tracker.record_trade(
                market_id=market_id,
                yes_price=yes_price,
                no_price=no_price,
                profit_margin=profit_margin,
                trade_size=self.config.trade_size
            )

            # Save to database
            self.db.save_trade(
                market_id, "YES", yes_shares, yes_price,
                self.config.trade_size, yes_order.get("orderID")
            )
            self.db.save_trade(
                market_id, "NO", no_shares, no_price,
                self.config.trade_size, no_order.get("orderID")
            )

            # Save position
            position = self.position_tracker.get_position(market_id)
            if position:
                self.db.save_position(position)

            self.logger.info(
                "Arbitrage executed: %s | Combined: $%.4f | Margin: %.2f%%",
                market_id[:16], yes_price + no_price, profit_margin * 100
            )

            # Send Telegram Notification asynchronously
            try:
                from src.telegram_notifier import send_arbitrage_executed
                pos_summary = self.position_tracker.get_summary()
                curr_exposure = pos_summary.get("total_exposure", 0.0)
                max_exposure = getattr(self.risk_manager.config, "max_total_exposure", 500.0)
                stats = self.stats_tracker.get_performance_summary()
                
                asyncio.create_task(send_arbitrage_executed(
                    market_id=market_id,
                    yes_price=yes_price,
                    no_price=no_price,
                    profit_margin=profit_margin,
                    trade_size=self.config.trade_size,
                    curr_exposure=curr_exposure,
                    max_exposure=max_exposure,
                    stats=stats
                ))
            except Exception as e:
                self.logger.error("Failed to schedule telegram message: %s", e)

            return True

        except Exception as e:
            self.logger.error("Arbitrage execution failed: %s", e)
            return False

    def get_status(self) -> Dict[str, Any]:
        """Get strategy status summary."""
        position_summary = self.position_tracker.get_summary()

        return {
            "strategy": "gabagool",
            "config": {
                "yes_threshold": self.config.yes_threshold,
                "no_threshold": self.config.no_threshold,
                "max_combined_cost": self.config.max_combined_cost,
                "trade_size": self.config.trade_size,
                "assets": self.config.assets
            },
            "positions": position_summary,
            "stats": self.stats_tracker.get_performance_summary()
        }
