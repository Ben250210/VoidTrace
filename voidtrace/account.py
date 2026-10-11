"""Your Warframe.Market account: sign in, and create / edit / delete your own buy and sell orders
and riven auctions.

How signing in works today: Warframe.Market has no OAuth for outside apps yet ("planned"), so this
uses the same email + password sign-in the website uses (POST /v1/auth/signin), which returns a
session token. That token is then sent as `Authorization: Bearer ...` to the v2 order endpoints, and
as `Authorization: JWT ...` to the v1 auction endpoints (rivens; v2 has none yet). The auction calls
were checked live on 2026-10-11 with one hidden test auction (created, edited, closed).

What is kept, and where:
  * the password is sent only to api.warframe.market and is never stored
  * the session token is stored in Windows Credential Manager (via `keyring`), not in any file
  * a random device id (Warframe.Market asks for one per install) lives in the VoidTrace cache dir

Nothing here runs on its own: every order is created by a click in the market window.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .items import default_cache_dir
from .market import API_BASE_V1, MarketClient
from .rivens import RivenAuction, RivenDraft, parse_auction

log = logging.getLogger(__name__)

KEYRING_SERVICE = "VoidTrace"
KEYRING_TOKEN = "warframe.market token"
KEYRING_USER = "warframe.market user"


class NotSignedIn(Exception):
    """No token, or Warframe.Market rejected it (expired / signed out elsewhere)."""


class MarketError(Exception):
    """Warframe.Market refused a request; the message says why in plain words."""


@dataclass(frozen=True)
class MyOrder:
    id: str
    item_id: str
    type: str          # sell / buy
    platinum: int
    quantity: int
    rank: Optional[int]
    visible: bool


class TokenStore:
    """Session token in Windows Credential Manager. Tests pass an in-memory dict instead."""

    def __init__(self, backend: Any = None) -> None:
        if backend is None:
            import keyring

            backend = keyring
        self._kr = backend

    def get(self) -> tuple[Optional[str], Optional[str]]:
        return (self._kr.get_password(KEYRING_SERVICE, KEYRING_TOKEN),
                self._kr.get_password(KEYRING_SERVICE, KEYRING_USER))

    def set(self, token: str, ingame_name: str) -> None:
        self._kr.set_password(KEYRING_SERVICE, KEYRING_TOKEN, token)
        self._kr.set_password(KEYRING_SERVICE, KEYRING_USER, ingame_name)

    def clear(self) -> None:
        for key in (KEYRING_TOKEN, KEYRING_USER):
            try:
                self._kr.delete_password(KEYRING_SERVICE, key)
            except Exception:
                pass  # already gone


def device_id(path: Optional[Path] = None) -> str:
    """A random id for this install, created once and reused (Warframe.Market asks for one)."""
    path = path or default_cache_dir() / "device.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))["device_id"]
    except (OSError, ValueError, KeyError):
        value = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"device_id": value}), encoding="utf-8")
        return value


# Plain words for the form errors Warframe.Market sends back on auctions.
AUCTION_ERRORS = {
    ("item", "name"): "That riven name does not match its buffs (Warframe.Market checks it).",
    ("item", "attributes"): "Warframe.Market did not accept those stats for this weapon.",
}


def _auction_error(resp) -> Optional[str]:
    try:
        error = resp.json().get("error")
    except (ValueError, AttributeError):
        return None
    if isinstance(error, dict):
        for (outer, inner), text in AUCTION_ERRORS.items():
            if isinstance(error.get(outer), dict) and inner in error[outer]:
                return text
    return None


def _error_message(resp) -> str:
    try:
        body = resp.json()
        detail = body.get("error") or body.get("errors") or body
    except ValueError:
        detail = resp.text[:200]
    return f"{resp.status_code}: {detail}"


class Account:
    def __init__(self, client: Optional[MarketClient] = None, store: Optional[TokenStore] = None,
                 device: Optional[str] = None) -> None:
        self.client = client or MarketClient()
        self.store = store or TokenStore()
        self._device = device
        self.token, self.ingame_name = self.store.get()

    @property
    def signed_in(self) -> bool:
        return bool(self.token)

    # --- sign in / out ------------------------------------------------------------

    def sign_in(self, email: str, password: str) -> str:
        """Sign in; returns your in-game name. The password is not kept anywhere."""
        resp = self.client.request(
            "POST", API_BASE_V1 + "/auth/signin",
            headers={"Authorization": "JWT", "Content-Type": "application/json"},
            json={"auth_type": "header", "email": email, "password": password,
                  "device_id": self._device or device_id()},
        )
        if resp.status_code in (400, 401, 403):
            raise MarketError("Warframe.Market did not accept that email/username and password.")
        if not resp.ok:
            raise MarketError(f"Sign-in failed ({_error_message(resp)}).")
        header = resp.headers.get("Authorization", "")
        token = header[4:].strip() if header.upper().startswith("JWT ") else header.strip()
        if not token:
            raise MarketError("Warframe.Market signed you in but sent no session token.")
        payload = resp.json().get("payload") or {}
        name = (payload.get("user") or {}).get("ingame_name") or (payload.get("user") or {}).get("ingameName") or ""
        self.token, self.ingame_name = token, name
        self.store.set(token, name)
        log.info("signed in to Warframe.Market as %s", name or "(unknown name)")
        return name

    def sign_out(self) -> None:
        """Forget the token on this PC (and tell Warframe.Market, best effort)."""
        if self.token:
            try:
                self.client.request("POST", "/auth/signout", headers=self._auth())
            except Exception:
                pass
        self.token = self.ingame_name = None
        self.store.clear()

    # --- your orders --------------------------------------------------------------------

    def my_orders(self) -> list[MyOrder]:
        return [_my_order(o) for o in self._call("GET", "/orders/my")]

    def create_order(self, item_id: str, order_type: str, platinum: int, quantity: int = 1,
                     rank: Optional[int] = None, visible: bool = True) -> MyOrder:
        if order_type not in ("sell", "buy"):
            raise ValueError("order_type must be 'sell' or 'buy'")
        if platinum < 1 or quantity < 1:
            raise ValueError("price and quantity must be at least 1")
        body = {"itemId": item_id, "type": order_type, "platinum": int(platinum),
                "quantity": int(quantity), "visible": visible}
        if rank is not None:
            body["rank"] = int(rank)
        return _my_order(self._call("POST", "/order", body))

    def update_order(self, order_id: str, **fields) -> MyOrder:
        allowed = {"platinum", "quantity", "rank", "visible", "perTrade"}
        body = {k: v for k, v in fields.items() if k in allowed and v is not None}
        return _my_order(self._call("PATCH", f"/order/{order_id}", body))

    def delete_order(self, order_id: str) -> None:
        self._call("DELETE", f"/order/{order_id}")

    # --- your riven auctions (v1) -------------------------------------------------------

    def my_auctions(self) -> list[RivenAuction]:
        """Your open riven auctions, hidden ones included."""
        payload = self._call_v1("GET", f"/profile/{self.ingame_name}/auctions")
        return [parse_auction(a, seller=self.ingame_name or "you") for a in payload.get("auctions", ())
                if not a.get("closed") and (a.get("item") or {}).get("type") == "riven"]

    def create_riven_auction(self, draft: RivenDraft, buyout: int, starting: Optional[int] = None,
                             minimal_reputation: int = 0, visible: bool = True, note: str = "") -> RivenAuction:
        if not draft.weapon or not draft.name or not draft.positives:
            raise ValueError("a riven needs its weapon, name and at least one buff")
        starting = buyout if starting is None else starting
        if buyout < 1 or not 1 <= starting <= buyout:
            raise ValueError("prices must be at least 1, and the starting price at most the buyout")
        body = {
            "note": note, "starting_price": int(starting), "buyout_price": int(buyout),
            "minimal_reputation": int(minimal_reputation), "visible": visible, "private": False,
            "item": {"type": "riven", "weapon_url_name": draft.weapon, "name": draft.name.lower(),
                     "mastery_level": int(draft.mr), "re_rolls": int(draft.rolls), "mod_rank": int(draft.rank),
                     "polarity": draft.polarity,
                     "attributes": [{"url_name": a.slug, "value": a.value, "positive": a.positive}
                                    for a in draft.attrs]},
        }
        created = parse_auction(self._call_v1("POST", "/auctions/create", body)["auction"], seller=self.ingame_name)
        if created.visible != visible:  # create does not always honour the flag; an edit does
            created = self.update_auction(created, visible=visible)
        return created

    def update_auction(self, auction: RivenAuction, **changes) -> RivenAuction:
        """Change price, visibility, reputation or note. The rest of the auction is sent unchanged."""
        body = {"buyout_price": auction.buyout, "starting_price": auction.starting, "visible": auction.visible,
                "minimal_reputation": auction.minimal_reputation, "note": auction.note}
        allowed = {"buyout_price", "starting_price", "visible", "minimal_reputation", "note"}
        body.update({k: v for k, v in changes.items() if k in allowed and v is not None})
        if body["buyout_price"] is not None and body["starting_price"] > body["buyout_price"]:
            body["starting_price"] = body["buyout_price"]  # repricing a direct sale moves both
        payload = self._call_v1("PUT", f"/auctions/entry/{auction.id}", body)
        return parse_auction(payload["auction"], seller=self.ingame_name)

    def close_auction(self, auction_id: str) -> None:
        self._call_v1("PUT", f"/auctions/entry/{auction_id}/close")

    # --- plumbing ------------------------------------------------------------------------

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        if not self.token:
            raise NotSignedIn("Sign in to Warframe.Market first.")
        resp = self.client.request(method, path, headers=self._auth(), json=body)
        if resp.status_code == 401:
            self.token = None
            self.store.clear()
            raise NotSignedIn("Your Warframe.Market session expired. Sign in again.")
        if not resp.ok:
            raise MarketError(f"Warframe.Market refused that ({_error_message(resp)}).")
        if not resp.content:
            return None
        return resp.json().get("data")

    def _call_v1(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        """Signed-in v1 call (auctions): JWT auth header, `payload` envelope."""
        if not self.token:
            raise NotSignedIn("Sign in to Warframe.Market first.")
        resp = self.client.request(method, API_BASE_V1 + path, json=body,
                                   headers={"Authorization": f"JWT {self.token}", "Content-Type": "application/json"})
        if resp.status_code == 401:
            self.token = None
            self.store.clear()
            raise NotSignedIn("Your Warframe.Market session expired. Sign in again.")
        if not resp.ok:
            raise MarketError(_auction_error(resp) or f"Warframe.Market refused that ({_error_message(resp)}).")
        return (resp.json() or {}).get("payload") or {}


def _my_order(o: dict) -> MyOrder:
    return MyOrder(
        id=o["id"],
        item_id=o.get("itemId", ""),
        type=o.get("type", ""),
        platinum=int(o.get("platinum", 0)),
        quantity=int(o.get("quantity", 1)),
        rank=o.get("rank"),
        visible=bool(o.get("visible", True)),
    )


# --- pricing ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class TopOrder:
    ingame_name: str
    platinum: int
    quantity: int
    rank: Optional[int]
    reputation: int
    status: str


def top_orders(client: MarketClient, slug: str, rank: Optional[int] = None) -> dict[str, list[TopOrder]]:
    """Top 5 sell and buy orders from players online right now, optionally for one rank only
    (the same panel warframe.market shows when you edit an order)."""
    query = f"?rank={rank}" if rank is not None else ""
    data = client.get(f"/orders/item/{slug}/top{query}")
    return {side: [TopOrder(o["user"].get("ingameName", "?"), int(o["platinum"]), int(o.get("quantity", 1)),
                            o.get("rank"), int(o["user"].get("reputation") or 0), o["user"].get("status", ""))
                   for o in data.get(side, [])]
            for side in ("sell", "buy")}


def suggested_price(top: dict[str, list[TopOrder]], order_type: str, offset: int = 0,
                    exclude: Optional[str] = None) -> Optional[int]:
    """Price to post at: the cheapest seller (when selling) or the highest buyer (when buying),
    plus `offset` (-1 undercuts by one plat). Never below 1. None when nobody is listed.
    `exclude` skips your own listing, so "match lowest" never just matches yourself."""
    me = (exclude or "").casefold()
    side = top.get("sell" if order_type == "sell" else "buy", [])
    prices = [o.platinum for o in side if not me or o.ingame_name.casefold() != me]
    if order_type == "sell":
        base = min(prices) if prices else None
    else:
        base = max(prices) if prices else None
    if base is None:
        return None
    return max(1, base + offset)
