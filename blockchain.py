"""
web3.py wrapper for optional on-chain transaction notarization on Sepolia.

Safe by design: if RPC_URL / PRIVATE_KEY / CONTRACT_ADDRESS aren't all set,
is_enabled() returns False and app.py skips notarization entirely - payments
work identically with or without this configured. Nothing here touches the
network unless all three env vars are present.
"""
import os
import json
import hashlib
import threading

RPC_URL = os.environ.get('RPC_URL')
PRIVATE_KEY = os.environ.get('PRIVATE_KEY')
CONTRACT_ADDRESS = os.environ.get('CONTRACT_ADDRESS')

_ABI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'contract_abi.json')

_w3 = None
_contract = None
_account = None


def is_enabled() -> bool:
    return bool(RPC_URL and PRIVATE_KEY and CONTRACT_ADDRESS)


def _get_client():
    global _w3, _contract, _account
    if _w3 is not None:
        return _w3, _contract, _account

    from web3 import Web3

    if not os.path.exists(_ABI_PATH):
        raise RuntimeError(
            "contract_abi.json not found - run scripts/deploy_contract.py once "
            "locally to deploy BioShieldLedger.sol to Sepolia and generate it."
        )

    with open(_ABI_PATH) as f:
        abi = json.load(f)

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise RuntimeError(f"Could not connect to RPC_URL: {RPC_URL}")

    account = w3.eth.account.from_key(PRIVATE_KEY)
    contract = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=abi)

    _w3, _contract, _account = w3, contract, account
    return _w3, _contract, _account


def _record_hash(txn_id, user_id, amount, recipient_upi, timestamp) -> bytes:
    """Deterministic hash of the fields that matter for integrity checking.
    Must stay in sync between notarize_async and verify_on_chain."""
    payload = f"{txn_id}|{user_id}|{amount}|{recipient_upi}|{timestamp}"
    return hashlib.sha256(payload.encode()).digest()


def notarize_async(txn_id, user_id, amount, recipient_upi, timestamp,
                    auth_method, risk_level, on_done):
    """Fire-and-forget: submits the notarize tx on a background thread so the
    payment response isn't held up waiting for block confirmation.
    Calls on_done(success: bool, tx_hash: str|None, block_number: int|None, error: str|None).
    """
    def _run():
        try:
            w3, contract, account = _get_client()
            record_hash = _record_hash(txn_id, user_id, amount, recipient_upi, timestamp)

            tx = contract.functions.notarize(txn_id, record_hash).build_transaction({
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address),
                'gas': 200000,
                'gasPrice': w3.eth.gas_price,
            })
            signed = account.sign_transaction(tx)
            raw = getattr(signed, 'raw_transaction', None) or signed.rawTransaction
            tx_hash = w3.eth.send_raw_transaction(raw)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

            on_done(receipt.status == 1, tx_hash.hex(), receipt.blockNumber, None)
        except Exception as e:
            on_done(False, None, None, str(e))

    threading.Thread(target=_run, daemon=True).start()


def verify_on_chain(txn_id, user_id, amount, recipient_upi, timestamp) -> bool:
    """Recomputes the record hash and compares it to what's stored on-chain.
    Raises RuntimeError if blockchain isn't configured - caller should turn
    that into a 503, not a crash."""
    if not is_enabled():
        raise RuntimeError("Blockchain notarization isn't configured on this deployment.")

    w3, contract, _ = _get_client()
    stored_hash, _ts, exists = contract.functions.getRecord(txn_id).call()
    if not exists:
        return False

    expected_hash = _record_hash(txn_id, user_id, amount, recipient_upi, timestamp)
    return stored_hash == expected_hash
