"""The tool services are callable without FastAPI (step 4 of the API brief)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.coupons import service as coupons
from app.database import Base
from app.pnl import service as pnl
from app.shop import service as shop

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
Session = sessionmaker(bind=engine)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    s = Session()
    yield s
    s.close()
    Base.metadata.drop_all(bind=engine)


def test_coupon_service_raises_domain_errors_not_http(db):
    with pytest.raises(coupons.NotFound):
        coupons.type_selections(db, "NOPE")
    with pytest.raises(coupons.Invalid):
        coupons.type_bundles(db, "TTS1")
    assert [t.code for t in coupons.coupon_types_board(db)][:2] == ["TTS1", "TTS2"]


def test_shop_service_caps_the_route(db, monkeypatch):
    monkeypatch.setattr(shop, "GOAL", 1)
    assert shop.log_visit(db, "u") is not None
    assert shop.log_visit(db, "u") is None
    assert shop.delete_visit(db, "someone-else", 1) is False


def test_pnl_service_csv(db):
    bet = pnl.new_bet(pnl.BetIn(stake=10, returned=25), "u", "dogs")
    db.add(bet)
    db.commit()
    text = pnl.bets_csv(pnl.ledger(db, "u", "dogs"))
    assert text.splitlines()[0].split(",") == pnl.CSV_COLUMNS
    assert ",15.00,10.00,25.00," in text
