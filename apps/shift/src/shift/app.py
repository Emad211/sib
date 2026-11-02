"""FastAPI application factory for the shift service."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from shift.__version__ import __version__
from shift.adapters.db.core import Database
from shift.api import (
    deps,
    routes_accountant,
    routes_auth,
    routes_badges,
    routes_campaigns,
    routes_catalog,
    routes_devices,
    routes_events,
    routes_handover,
    routes_held,
    routes_manager,
    routes_on_shift,
    routes_photos,
    routes_purchase,
    routes_rewards,
    routes_settings,
    routes_shift,
    routes_web,
)
from shift.api.errors import install_error_handlers
from shift.common.clock import Clock, SystemClock, to_tehran
from shift.config.logging import configure_logging
from shift.config.settings import Settings
from shift.domain.daily_card import FRIDAY
from shift.domain.permissions import Permission
from shift.runtime import BackgroundJobs
from shift.services.accountant_queue import AccountantQueueService
from shift.services.accounting_bridge import AccountingBridge
from shift.services.audit import AuditService
from shift.services.auth import AuthService
from shift.services.backup import BackupService
from shift.services.campaign_report import CampaignReportService
from shift.services.campaigns import CampaignService
from shift.services.catalog import CatalogService
from shift.services.daily_card import DailyCardService
from shift.services.devices import DeviceService
from shift.services.event_store import EventStore
from shift.services.handover import HandoverService
from shift.services.held_events import HeldEvents
from shift.services.login_throttle import LoginThrottle
from shift.services.manager_decisions import ManagerDecisions
from shift.services.photos import PhotoStore
from shift.services.projections import ProjectionEngine
from shift.services.purchase import PurchaseService
from shift.services.rewards import RewardsService
from shift.services.scheduler import DAILY_CARD, PolicySchedule, Scheduler
from shift.services.settings import SettingsService
from shift.services.shift_events import ShiftEventService
from shift.services.two_step import TwoStepService


def _catalog_service(request: Request) -> CatalogService:
    """The catalog service created in the lifespan (manager routes only)."""
    return request.app.state.catalog


def _settings_service(request: Request) -> SettingsService:
    """The settings service created in the lifespan (manager routes only)."""
    return request.app.state.settings_service


def _daily_card_service(request: Request) -> DailyCardService:
    """The daily card service created in the lifespan (the manager's card pages)."""
    return request.app.state.daily_card


def _campaign_service(request: Request) -> CampaignService:
    """The campaign calendar created in the lifespan (manager API and page)."""
    return request.app.state.campaigns


def _accountant_queue_service(request: Request) -> AccountantQueueService:
    """The accountant's queue created in the lifespan (SHF-5)."""
    return request.app.state.accountant_queue


def _rewards_service(request: Request) -> RewardsService:
    """The reward ledger created in the lifespan (the manager's screen and one person's own)."""
    return request.app.state.rewards


def _shift_events_service(request: Request) -> ShiftEventService:
    """The one validated shift-event writer, shared with the reward revocation form (SHF-5)."""
    return request.app.state.shift_events


def _campaign_report_service(request: Request) -> CampaignReportService:
    """The campaign report created in the lifespan (SHF-5, finding F8)."""
    return request.app.state.campaign_report


def _daily_card_action(cards: DailyCardService) -> Callable[[datetime], str]:
    """The scheduler's card action: store every due card and report what it stored (SHF-6.1).

    The scheduler decides *when* and this decides *what*; its return value becomes
    `TaskRun.message` and the runtime's log line, so it stays one short sentence. The scheduler never
    calls it on Friday, yet the empty answer keeps the message honest if it ever does (ADR-018).
    """

    def generate(moment: datetime) -> str:
        stored = cards.generate_due(moment)
        if not stored:
            # Two different quiet mornings: clinic policy (Friday, ADR-018), or a restart after 07:30
            # that found today's card already stored. The log line has to say which one happened.
            if to_tehran(moment).weekday() == FRIDAY:
                return "no card today (Friday)"
            return "daily card already up to date"
        newest = stored[-1]
        days = newest.day_count
        message = f"daily card {newest.card_date.isoformat()} stored ({days} day{'s' if days > 1 else ''})"
        if len(stored) > 1:
            message += f", {len(stored) - 1} missed backfilled"
        return message

    return generate


def _background_jobs(
    settings: Settings,
    database: Database,
    *,
    clock: Clock,
    services: SettingsService,
    audit: AuditService,
    bridge: AccountingBridge | None,
    cards: DailyCardService,
) -> BackgroundJobs | None:
    """The jobs a serving process runs, or `None` when this process only answers requests.

    Backups follow ADR-008: one snapshot at startup and one a day at 04:00 Tehran. The accounting
    bridge is the one the handover service also reads through, and it only exists when a copy is
    configured; it reads that copy read-only. The scheduler's card action is the real builder, so a
    due card is stored the moment it falls due and not by a page request.
    """
    if not settings.background_jobs:
        return None
    return BackgroundJobs(
        clock=clock,
        scheduler=Scheduler(
            PolicySchedule(services),
            clock=clock,
            actions={DAILY_CARD: _daily_card_action(cards)},
        ),
        backup=BackupService(
            settings.database_path,
            backup_dir=settings.resolved_backup_dir,
            keep_snapshots=settings.backup_keep,
            mirror_dir=settings.backup_mirror_dir,
            photos_dir=settings.resolved_photos_dir,
            clock=clock,
        ),
        audit=audit,
        bridge=bridge,
        accounting_path=settings.accounting_db_path,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application with logging, database and secrets initialised."""
    resolved = settings or Settings.from_env()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        clock = SystemClock()
        database = Database(resolved.database_path)
        database.migrate()
        application.state.clock = clock
        application.state.login_throttle = LoginThrottle(clock=clock)
        # Every attempt costs a 64 MB hash, so the whole clinic gets a ceiling too: a flood of invented
        # mobiles must not exhaust the machine the handover runs on (it is far above a real morning).
        application.state.login_ceiling = LoginThrottle(clock=clock, attempts=90)
        # A phone sends a handful of receipt photos an hour; a flood would fill the clinic computer's disk.
        application.state.photo_throttle = LoginThrottle(clock=clock, attempts=60, window=timedelta(minutes=10))
        application.state.database = database
        application.state.events = EventStore(database, clock=clock, hash_chain=resolved.hash_chain)
        application.state.projections = ProjectionEngine(database, clock=clock)
        application.state.secret_key = resolved.read_or_create_secret()
        # Every audited action of the services below lands in `audit_log` and in `audit.log`.
        audit = AuditService(database, clock=clock)
        application.state.audit = audit
        application.state.two_step = TwoStepService(database, key=application.state.secret_key, clock=clock)
        application.state.auth = AuthService(
            database,
            clock=clock,
            pepper=application.state.secret_key,
            audit=audit,
            two_step=application.state.two_step,
        )
        application.state.devices = DeviceService(database, clock=clock, audit=audit)
        application.state.catalog = CatalogService(database, clock=clock)
        application.state.settings_service = SettingsService(database, clock=clock)
        # The receipt photos (SHF-7): files on this PC, one per content hash, never an external service.
        application.state.photos = PhotoStore(resolved.resolved_photos_dir)
        # The manager's campaign calendar is the only source of a «ت» discount (S01 §3).
        application.state.campaigns = CampaignService(database, clock=clock)
        # F5: a record whose own instant is implausible is kept with the manager, never dropped.
        application.state.held = HeldEvents(database, application.state.events, clock=clock)
        # The staff phone's shift events (SHF-3): one validated, idempotent writer for every kind.
        application.state.shift_events = ShiftEventService(
            database,
            application.state.events,
            application.state.projections,
            application.state.catalog,
            application.state.settings_service,
            campaigns=application.state.campaigns,
            clock=clock,
            held=application.state.held,
        )
        # The manager's daily card (SHF-6.1) reads the log, the flows and the audit trail and stores
        # one document per day; the scheduler's action below is the only thing that generates it.
        application.state.daily_card = DailyCardService(database, clock=clock, audit=audit)
        # The reward ledger (SHF-5): rates, accrual from the accounting mirrors, the derived month
        # ledger and the audit of every manager change. It reads the log, never rewrites it.
        application.state.rewards = RewardsService(database, application.state.events, clock=clock, audit=audit)
        # The accountant's queue and the campaign report (SHF-5): both read the log, never write it.
        application.state.accountant_queue = AccountantQueueService(
            database,
            events=application.state.shift_events,
            settings=application.state.settings_service,
            rewards=application.state.rewards,
            clock=clock,
        )
        application.state.campaign_report = CampaignReportService(
            database, campaigns=application.state.campaigns, clock=clock
        )
        # The purchasing chain and the warehouse (SHF-4): one service writes every action and folds the
        # state the page reads, so the log stays the only truth of the chain.
        application.state.purchase = PurchaseService(
            database,
            application.state.events,
            application.state.projections,
            application.state.catalog,
            application.state.settings_service,
            clock=clock,
            photos=application.state.photos,
        )
        # One bridge for the process: the handover start reads accounting through it, and the
        # background poll keeps the mirror live. Without an accounting copy the bridge stays None
        # and a handover start is refused (H-03), never silently allowed.
        accounting_path = resolved.accounting_db_path
        bridge = None
        if accounting_path is not None:
            bridge = AccountingBridge(database, application.state.events, clock=clock)
        application.state.handover = HandoverService(
            database,
            application.state.events,
            projections=application.state.projections,
            catalog=application.state.catalog,
            audit=audit,
            clock=clock,
            bridge=bridge,
            accounting_path=None if accounting_path is None else str(accounting_path),
        )
        application.state.decisions = ManagerDecisions(
            database,
            application.state.events,
            application.state.handover,
            application.state.catalog,
            clock=clock,
        )
        jobs = _background_jobs(
            resolved,
            database,
            clock=clock,
            services=application.state.settings_service,
            audit=audit,
            bridge=bridge,
            cards=application.state.daily_card,
        )
        application.state.jobs = jobs
        if jobs is not None:
            await jobs.start()
        try:
            yield
        finally:
            if jobs is not None:
                await jobs.stop()
            database.close()

    app = FastAPI(
        title="Sib Shift",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = resolved
    install_error_handlers(app)

    @app.middleware("http")
    async def default_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Every page carries the baseline headers, so a route that forgets them (the login, the pairing
        page, the device admin) is never framed or sniffed; a route that sets its own keeps it (S-4, S-17)."""
        response = await call_next(request)
        for name, value in routes_web.BASELINE_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/static/"):
            # No header at all lets a phone keep an old script for hours (heuristic freshness) and an
            # update never lands. Scripts and styles are re-checked each load (a cheap 304 by ETag);
            # fonts and icons only change with a new file name, so they may be kept a day.
            kept = request.url.path.startswith(("/static/fonts/", "/static/icons/"))
            response.headers.setdefault("Cache-Control", "public, max-age=86400" if kept else "no-cache")
        if response.headers.get("content-type", "").startswith("text/html"):
            # The pages without their own policy (login, pairing) still run inline code, so the default
            # only forbids framing; the full policy stays with the pages that were written for it.
            response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response

    app.include_router(routes_auth.router)
    app.include_router(routes_badges.router)
    app.include_router(routes_shift.router)
    app.include_router(routes_devices.router)
    app.include_router(
        routes_catalog.build_catalog_router(
            guard=deps.require(Permission.CATALOG_MANAGE),
            catalog=_catalog_service,
            running_shift=deps.running_shift,
        )
    )
    app.include_router(
        routes_settings.build_settings_router(
            guard=deps.require(Permission.SETTINGS_CHANGE),
            settings=_settings_service,
            running_shift=deps.running_shift,
        )
    )
    # The manager's card and settings pages (SHF-6.1): server-rendered, one page guard each.
    app.include_router(routes_manager.build_manager_router(cards=_daily_card_service, settings=_settings_service))
    # The campaign calendar behind the shift event «ت» (SHF-3): manager API and manager page.
    app.include_router(
        routes_campaigns.build_campaign_router(
            guard=deps.require(Permission.CATALOG_MANAGE),
            page_guard=deps.require_page(Permission.SETTINGS_CHANGE),
            campaigns=_campaign_service,
        )
    )
    # The accountant's queue and the campaign report (SHF-5, finding F8): one page guard each.
    app.include_router(
        routes_accountant.build_accountant_router(queue=_accountant_queue_service, report=_campaign_report_service)
    )
    # The manager's reward screen and one person's own standing (SHF-5): the confirmation buttons
    # post SHF-3's own decision endpoint, and the revocation goes through the same validated writer.
    app.include_router(
        routes_rewards.build_rewards_router(rewards=_rewards_service, shift_events=_shift_events_service)
    )

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(routes_events.router)
    # ADR-021: the one button a second receptionist or the shift doctor presses to come on shift.
    app.include_router(routes_on_shift.router)
    # The receipt photos (SHF-7): upload by the recorder, read by the manager and the approvers.
    app.include_router(routes_photos.router)
    app.include_router(routes_handover.router)
    app.include_router(routes_held.router)
    app.include_router(routes_purchase.router)
    app.include_router(routes_web.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness probe used by the deployment wrapper and CI smoke test."""
        return {"status": "ok", "version": __version__}

    return app
