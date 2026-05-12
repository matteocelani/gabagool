#!/usr/bin/env python3
"""
Gabagool Bot - Main Entry Point

Purpose:
    Main entry point and orchestrator for the Gabagool arbitrage bot.
    Initializes all components and runs the main trading loop.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Dependencies:
    - asyncio
    - logging
    - All src modules

Usage:
    # Dry run mode
    python -m src.main --dry-run

    # Production mode
    python -m src.main

    # With custom config
    python -m src.main --config config/production.yaml

Notes:
    - Components are initialized from multiple source repositories
    - See README.md for architecture overview
    - Run in tmux/screen for production

Data Sources:
    - Config: config/default.yaml, config/production.yaml
    - Environment: config/.env
    - Database: gabagool.db
"""

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Core infrastructure (Phase 1 - from discountry)
from src.config import Config
from src.bot import TradingBot, BotConfig, create_bot_from_config

# Strategy components (already implemented)
from src.position_tracker import PositionTracker, ArbitragePosition
from src.risk_manager import RiskManager, RiskConfig
from src.stats_tracker import StatsTracker
from src.db import TradingDatabase

# Strategy
from strategies.gabagool_strategy import GabagoolStrategy


# Constants
VERSION = "1.0.0"
DEFAULT_CONFIG = "config/default.yaml"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Global flag for shutdown
shutdown_event = asyncio.Event()


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> None:
    """
    Configure logging for the bot.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory for log files
    """
    # Create handlers
    handlers = [logging.StreamHandler()]

    # File handler
    log_path = PROJECT_ROOT / log_dir / "gabagool.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(log_path))

    # Configure
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=LOG_FORMAT,
        handlers=handlers
    )

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Gabagool - Polymarket Arbitrage Bot"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_CONFIG,
        help="Path to configuration file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in simulation mode without executing trades"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=5,
        help="Seconds between opportunity scans (default: 5)"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Gabagool Bot v{VERSION}"
    )
    return parser.parse_args()


def setup_signal_handlers() -> None:
    """Setup graceful shutdown handlers for SIGINT and SIGTERM."""
    def handler(sig, frame):
        logging.getLogger("main").info("Shutdown signal received (%s)", sig)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


class GabagoolBot:
    """
    Main orchestrator for the Gabagool arbitrage bot.

    Wires together all components:
    - TradingBot (from discountry): Order execution
    - PositionTracker (from trust412): Position management
    - RiskManager (from lorine93s): Pre-trade validation
    - StatsTracker (from warproxxx): Performance metrics
    - TradingDatabase: SQLite persistence
    - GabagoolStrategy: Arbitrage logic
    """

    def __init__(self, config: Config, dry_run: bool = False):
        """
        Initialize all components.

        Args:
            config: Config object with all settings
            dry_run: If True, simulate trades without executing
        """
        self.config = config
        self.dry_run = dry_run or config.dry_run
        self.logger = logging.getLogger("GabagoolBot")

        # Override dry_run in config if CLI flag set
        if dry_run:
            self.config.dry_run = True

        # Components (initialized in start())
        self.bot: Optional[TradingBot] = None
        self.position_tracker: Optional[PositionTracker] = None
        self.risk_manager: Optional[RiskManager] = None
        self.stats_tracker: Optional[StatsTracker] = None
        self.db: Optional[TradingDatabase] = None
        self.strategy: Optional[GabagoolStrategy] = None

        # State
        self.consecutive_failures = 0
        self.starting_balance = 0.0

    def initialize_components(self) -> bool:
        """
        Initialize all components.

        Returns:
            True if all components initialized successfully
        """
        self.logger.info("Initializing components...")

        try:
            # 1. Database (first, for persistence)
            db_path = PROJECT_ROOT / self.config.db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db = TradingDatabase(str(db_path))
            self.logger.info("  [OK] Database: %s", db_path)

            # 2. Trading Bot (from discountry base)
            self.bot = create_bot_from_config(self.config)
            self.logger.info("  [OK] TradingBot (discountry)")

            # 3. Position Tracker (from trust412)
            self.position_tracker = PositionTracker(
                max_concurrent=self.config.gabagool.max_concurrent_arbitrages
            )
            self.logger.info("  [OK] PositionTracker (trust412)")

            # 4. Risk Manager (from lorine93s)
            risk_config = RiskConfig(
                max_position_per_market=self.config.gabagool.max_position_per_market,
                max_total_exposure=self.config.gabagool.max_total_exposure,
                max_concurrent_arbitrages=self.config.gabagool.max_concurrent_arbitrages,
                min_profit_margin=self.config.gabagool.min_profit_margin,
                max_combined_cost=self.config.gabagool.max_combined_cost,
            )
            self.risk_manager = RiskManager(risk_config)
            self.logger.info("  [OK] RiskManager (lorine93s)")

            # 5. Stats Tracker (from warproxxx)
            stats_path = PROJECT_ROOT / self.config.data_dir / "performance_stats.json"
            stats_path.parent.mkdir(parents=True, exist_ok=True)
            self.stats_tracker = StatsTracker(str(stats_path))
            self.logger.info("  [OK] StatsTracker (warproxxx)")

            # 6. Load active positions from DB
            self._restore_positions()

            # 7. Strategy
            strategy_config = {
                "yes_threshold": self.config.gabagool.yes_buy_threshold,
                "no_threshold": self.config.gabagool.no_buy_threshold,
                "max_combined_cost": self.config.gabagool.max_combined_cost,
                "min_profit_margin": self.config.gabagool.min_profit_margin,
                "trade_size": self.config.gabagool.max_position_per_market / 2,
                "assets": self.config.gabagool.target_assets,
            }
            self.strategy = GabagoolStrategy(
                bot=self.bot,
                config=strategy_config,
                position_tracker=self.position_tracker,
                risk_manager=self.risk_manager,
                stats_tracker=self.stats_tracker,
                db=self.db
            )
            self.logger.info("  [OK] GabagoolStrategy")

            self.logger.info("All components initialized successfully")
            return True

        except Exception as e:
            self.logger.error("Component initialization failed: %s", e)
            return False

    def _restore_positions(self) -> None:
        """Restore active positions from database on restart."""
        try:
            rows = self.db.load_active_positions()
            restored = 0

            for row in rows:
                # Recreate ArbitragePosition from database row
                pos = ArbitragePosition(
                    market_id=row["market_id"],
                    yes_token_id=row["yes_token_id"] or "",
                    no_token_id=row["no_token_id"] or "",
                    yes_shares=row["yes_shares"] or 0,
                    yes_avg_cost=row["yes_avg_cost"] or 0,
                    yes_total_cost=row["yes_total_cost"] or 0,
                    no_shares=row["no_shares"] or 0,
                    no_avg_cost=row["no_avg_cost"] or 0,
                    no_total_cost=row["no_total_cost"] or 0,
                    holding_time_limit=row["holding_time_limit"] or 1800,
                )
                self.position_tracker.active_positions[pos.market_id] = pos
                restored += 1

            if restored > 0:
                self.logger.info("  Restored %d active positions from database", restored)

        except Exception as e:
            self.logger.warning("Could not restore positions: %s", e)

    def connect(self) -> bool:
        """
        Connect to Polymarket API.

        Returns:
            True if connected successfully
        """
        self.logger.info("Connecting to Polymarket...")

        if not self.bot:
            self.logger.error("Bot not initialized")
            return False

        if not self.bot.connect():
            self.logger.error("Failed to connect to Polymarket API")
            return False

        self.logger.info("Connected to Polymarket API")
        return True

    async def run_loop(self, scan_interval: int = 5) -> None:
        """
        Main trading loop.

        Args:
            scan_interval: Seconds between opportunity scans
        """
        self.logger.info("-" * 60)
        self.logger.info("Starting main trading loop (scan every %ds)", scan_interval)
        self.logger.info("-" * 60)

        loop_count = 0
        last_summary_hour = -1

        while not shutdown_event.is_set():
            loop_count += 1
            
            # Send periodic telegram summary at 00:00, 08:00, 16:00
            try:
                from datetime import datetime
                current_hour = datetime.now().hour
                if current_hour in [0, 8, 16] and current_hour != last_summary_hour:
                    from src.telegram_notifier import send_status_update
                    pos_summary = self.position_tracker.get_summary()
                    curr_exposure = pos_summary.get("total_exposure", 0.0)
                    max_exposure = getattr(self.risk_manager.config, "max_total_exposure", 500.0)
                    stats = self.stats_tracker.get_performance_summary()
                    
                    asyncio.create_task(send_status_update(curr_exposure, max_exposure, stats, self.consecutive_failures))
                    last_summary_hour = current_hour
            except Exception as e:
                self.logger.error("Failed to schedule periodic telegram summary: %s", e)

            try:
                # Check circuit breakers
                daily_pnl = self.stats_tracker.get_performance_summary().get("total_profit", 0)
                can_continue, reason = self.risk_manager.check_circuit_breakers(
                    daily_pnl=daily_pnl,
                    starting_balance=self.starting_balance,
                    consecutive_failures=self.consecutive_failures,
                    wallet_balance=100.0  # TODO: Get actual balance
                )

                if not can_continue:
                    self.logger.warning("Circuit breaker triggered: %s", reason)
                    
                    # Alert on circuit breaker
                    from src.telegram_notifier import send_error_alert
                    asyncio.create_task(send_error_alert(reason, "Circuit Breaker Triggered"))
                    
                    # Wait longer before retrying
                    await asyncio.sleep(60)
                    continue

                # Scan for opportunities
                # Note: strategy.scan_opportunities() is async but calls sync bot methods
                executed = await self.strategy.scan_opportunities()

                if executed > 0:
                    self.consecutive_failures = 0
                    self.logger.info("Executed %d arbitrage(s) in loop %d", executed, loop_count)

                # Cleanup expired positions
                cleaned = self.position_tracker.cleanup_expired()
                if cleaned > 0:
                    self.logger.info("Cleaned up %d expired positions", cleaned)

                # Periodic status (every 100 loops)
                if loop_count % 100 == 0:
                    self._log_status()

            except Exception as e:
                self.consecutive_failures += 1
                self.logger.error("Error in trading loop: %s", e)
                
                # Send error alert to telegram
                try:
                    from src.telegram_notifier import send_error_alert
                    asyncio.create_task(send_error_alert(str(e), "Main Trading Loop Exception"))
                except Exception as alert_err:
                    self.logger.error("Failed to send error alert: %s", alert_err)

                # If too many failures, increase backoff
                if self.consecutive_failures >= 5:
                    self.logger.warning("Multiple failures, backing off...")
                    await asyncio.sleep(30)

            # Wait before next scan
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=scan_interval
                )
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue loop

        # Shutdown
        self.logger.info("Trading loop stopped")

    def _log_status(self) -> None:
        """Log periodic status update."""
        pos_summary = self.position_tracker.get_summary()
        stats_summary = self.stats_tracker.get_performance_summary()

        self.logger.info("-" * 40)
        self.logger.info("STATUS UPDATE")
        self.logger.info(
            "  Positions: %d active (%d complete, %d incomplete)",
            pos_summary["total_positions"],
            pos_summary["complete_pairs"],
            pos_summary["incomplete_pairs"]
        )
        self.logger.info(
            "  Exposure: $%.2f / $%.2f",
            pos_summary["total_exposure"],
            self.config.gabagool.max_total_exposure
        )
        self.logger.info(
            "  Trades: %d total, %.1f%% win rate",
            stats_summary["total_trades"],
            stats_summary["win_rate"] * 100
        )
        self.logger.info(
            "  Profit: $%.2f",
            stats_summary["total_profit"]
        )
        self.logger.info("-" * 40)

    def shutdown(self) -> None:
        """Graceful shutdown."""
        self.logger.info("Shutting down...")

        # Print final stats
        if self.stats_tracker:
            self.stats_tracker.print_summary()

        # Save any pending positions
        if self.position_tracker and self.db:
            for pos in self.position_tracker.active_positions.values():
                self.db.save_position(pos)

        # Close database
        if self.db:
            self.db.close()

        self.logger.info("Shutdown complete")


async def main():
    """Main entry point for the Gabagool bot."""
    args = parse_args()

    # Setup
    setup_logging(level=args.log_level)
    setup_signal_handlers()
    logger = logging.getLogger("main")

    # Banner
    logger.info("=" * 60)
    logger.info("GABAGOOL ARBITRAGE BOT - v%s", VERSION)
    logger.info("Hybrid approach with best-in-class components")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info(">>> DRY RUN MODE - No trades will be executed <<<")

    # Load configuration
    try:
        config = Config.load_with_env(args.config)
        logger.info("Configuration loaded: %s", config)
    except Exception as e:
        logger.error("Failed to load configuration: %s", e)
        sys.exit(1)

    # Validate configuration
    errors = config.validate()
    if errors:
        logger.error("Configuration validation failed:")
        for error in errors:
            logger.error("  - %s", error)
        sys.exit(1)

    # Create and initialize bot
    gabagool = GabagoolBot(config, dry_run=args.dry_run)

    if not gabagool.initialize_components():
        logger.error("Failed to initialize components")
        sys.exit(1)

    # Connect to API (skip in dry_run for testing)
    if not args.dry_run:
        if not gabagool.connect():
            logger.error("Failed to connect to Polymarket")
            sys.exit(1)
    else:
        logger.info("Skipping API connection in dry-run mode")

    # Run main loop
    try:
        await gabagool.run_loop(scan_interval=args.scan_interval)
    finally:
        gabagool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
