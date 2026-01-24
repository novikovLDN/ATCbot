"""
Failure domains for disaster recovery planning.

This module defines failure domains, their blast radius,
recovery strategies, and maximum tolerated downtime.

IMPORTANT:
- Failure domains are for planning and documentation
- They do NOT affect runtime behavior
- They are used for runbooks and incident response
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum
from datetime import timedelta


class FailureDomain(str, Enum):
    """Failure domains in the system"""
    DATABASE = "database"
    VPN_API = "vpn_api"
    PAYMENTS = "payments"
    TELEGRAM_API = "telegram_api"
    BACKGROUND_WORKERS = "background_workers"


class RecoveryStrategy(str, Enum):
    """Recovery strategies for failure domains"""
    AUTOMATIC = "automatic"  # System recovers automatically
    MANUAL = "manual"  # Requires operator intervention
    GRACEFUL_DEGRADATION = "graceful_degradation"  # System continues with reduced functionality
    FAIL_SAFE = "fail_safe"  # System stops safely, no data loss


@dataclass
class FailureDomainConfig:
    """
    Configuration for a failure domain.
    
    Defines blast radius, recovery strategy, and MTTD.
    """
    domain: FailureDomain
    blast_radius: str  # Description of what is affected
    recovery_strategy: RecoveryStrategy
    max_tolerated_downtime_minutes: int  # MTTD in minutes
    automatic_behavior: str  # What system does automatically
    operator_actions: List[str]  # What operator must do
    rollback_steps: List[str]  # How to rollback
    data_safety_guarantees: str  # What data is safe


class FailureDomainRegistry:
    """
    Registry of failure domains and their configurations.
    
    Used for disaster recovery planning and runbook generation.
    """
    
    def __init__(self):
        """Initialize failure domain registry"""
        self._domains: Dict[FailureDomain, FailureDomainConfig] = {
            FailureDomain.DATABASE: FailureDomainConfig(
                domain=FailureDomain.DATABASE,
                blast_radius="All database operations, subscriptions, payments, user data",
                recovery_strategy=RecoveryStrategy.GRACEFUL_DEGRADATION,
                max_tolerated_downtime_minutes=30,  # 30 minutes MTTD
                automatic_behavior=(
                    "System enters UNAVAILABLE state. "
                    "Background workers skip iterations. "
                    "Handlers continue but cannot process requests requiring DB. "
                    "Cooldown activates after recovery. "
                    "Warm-up iterations start automatically."
                ),
                operator_actions=[
                    "Check database connection pool status",
                    "Verify PostgreSQL service health",
                    "Check network connectivity",
                    "Review database logs for errors",
                    "Restart database service if needed",
                    "Verify connection pool recovery",
                ],
                rollback_steps=[
                    "Restore from backup if data corruption detected",
                    "Rollback database schema changes if applicable",
                    "Verify data integrity after restore",
                ],
                data_safety_guarantees=(
                    "No data loss during graceful degradation. "
                    "Pending operations queued in memory (non-persistent). "
                    "Committed transactions are safe. "
                    "Uncommitted transactions may be lost."
                ),
            ),
            FailureDomain.VPN_API: FailureDomainConfig(
                domain=FailureDomain.VPN_API,
                blast_radius="VPN key generation, UUID management, VPN access provisioning",
                recovery_strategy=RecoveryStrategy.GRACEFUL_DEGRADATION,
                max_tolerated_downtime_minutes=60,  # 1 hour MTTD
                automatic_behavior=(
                    "System enters DEGRADED state. "
                    "VPN API calls fail gracefully. "
                    "Subscriptions created with 'pending' activation status. "
                    "Activation worker retries automatically. "
                    "Users can still use existing VPN keys."
                ),
                operator_actions=[
                    "Check VPN API endpoint health",
                    "Verify Xray Core service status",
                    "Check network connectivity to VPN server",
                    "Review VPN API logs",
                    "Restart Xray Core if needed",
                ],
                rollback_steps=[
                    "Revert VPN API configuration changes",
                    "Restore Xray Core from backup if needed",
                ],
                data_safety_guarantees=(
                    "Existing VPN keys remain functional. "
                    "New subscriptions are created but not activated. "
                    "No user data loss. "
                    "Activation retries automatically after recovery."
                ),
            ),
            FailureDomain.PAYMENTS: FailureDomainConfig(
                domain=FailureDomain.PAYMENTS,
                blast_radius="Payment processing, balance top-ups, subscription renewals",
                recovery_strategy=RecoveryStrategy.GRACEFUL_DEGRADATION,
                max_tolerated_downtime_minutes=15,  # 15 minutes MTTD
                automatic_behavior=(
                    "Payment provider calls fail gracefully. "
                    "Payment status tracked but not finalized. "
                    "Retries bounded and logged. "
                    "Users see payment pending status."
                ),
                operator_actions=[
                    "Check payment provider API status",
                    "Verify payment provider credentials",
                    "Review payment logs for errors",
                    "Check network connectivity",
                    "Contact payment provider support if needed",
                ],
                rollback_steps=[
                    "Revert payment provider configuration",
                    "Reconcile payment status manually if needed",
                ],
                data_safety_guarantees=(
                    "Payment records are created but not finalized. "
                    "No duplicate charges. "
                    "Idempotency prevents double-processing. "
                    "Manual reconciliation possible after recovery."
                ),
            ),
            FailureDomain.TELEGRAM_API: FailureDomainConfig(
                domain=FailureDomain.TELEGRAM_API,
                blast_radius="User notifications, bot responses, admin alerts",
                recovery_strategy=RecoveryStrategy.GRACEFUL_DEGRADATION,
                max_tolerated_downtime_minutes=10,  # 10 minutes MTTD
                automatic_behavior=(
                    "Telegram API calls fail gracefully. "
                    "Messages queued in memory (non-persistent). "
                    "System continues processing. "
                    "Retries bounded and logged."
                ),
                operator_actions=[
                    "Check Telegram Bot API status",
                    "Verify bot token validity",
                    "Check network connectivity",
                    "Review Telegram API rate limits",
                ],
                rollback_steps=[
                    "Revert bot token changes if applicable",
                ],
                data_safety_guarantees=(
                    "No data loss. "
                    "Messages may be delayed but not lost. "
                    "System continues processing business logic."
                ),
            ),
            FailureDomain.BACKGROUND_WORKERS: FailureDomainConfig(
                domain=FailureDomain.BACKGROUND_WORKERS,
                blast_radius="Subscription activation, expiry cleanup, payment watching, reminders",
                recovery_strategy=RecoveryStrategy.AUTOMATIC,
                max_tolerated_downtime_minutes=5,  # 5 minutes MTTD
                automatic_behavior=(
                    "Workers skip iterations during system unavailability. "
                    "Workers resume automatically after recovery. "
                    "Warm-up iterations prevent overload. "
                    "Cooldown prevents thrashing."
                ),
                operator_actions=[
                    "Check worker process status",
                    "Review worker logs for errors",
                    "Verify system state transitions",
                    "Check for stuck iterations",
                ],
                rollback_steps=[
                    "Restart worker processes if needed",
                    "Clear stuck state if applicable",
                ],
                data_safety_guarantees=(
                    "No data loss. "
                    "Delayed processing only. "
                    "All operations eventually processed. "
                    "Idempotency ensures correctness."
                ),
            ),
        }
    
    def get_domain(self, domain: FailureDomain) -> FailureDomainConfig:
        """
        Get configuration for a failure domain.
        
        Args:
            domain: Failure domain
            
        Returns:
            FailureDomainConfig for the domain
        """
        return self._domains.get(domain)
    
    def get_all_domains(self) -> Dict[FailureDomain, FailureDomainConfig]:
        """Get all failure domain configurations"""
        return dict(self._domains)
    
    def get_domains_by_strategy(
        self,
        strategy: RecoveryStrategy
    ) -> List[FailureDomainConfig]:
        """
        Get all domains with a specific recovery strategy.
        
        Args:
            strategy: Recovery strategy
            
        Returns:
            List of domain configurations
        """
        return [
            config for config in self._domains.values()
            if config.recovery_strategy == strategy
        ]


# Global singleton instance
_failure_domain_registry: Optional[FailureDomainRegistry] = None


def get_failure_domain_registry() -> FailureDomainRegistry:
    """
    Get or create global failure domain registry instance.
    
    Returns:
        Global FailureDomainRegistry instance
    """
    global _failure_domain_registry
    
    if _failure_domain_registry is None:
        _failure_domain_registry = FailureDomainRegistry()
    
    return _failure_domain_registry
