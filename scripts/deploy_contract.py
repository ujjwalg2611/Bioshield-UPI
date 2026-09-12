"""
One-time script: compiles contracts/BioShieldLedger.sol and deploys it to
Sepolia, then writes contract_abi.json to the project root (blockchain.py
reads this file at runtime - it must exist before notarization will work).

Usage (PowerShell):
    $env:RPC_URL="https://sepolia.infura.io/v3/<project-id>"
    $env:PRIVATE_KEY="0x<funded-sepolia-testnet-private-key>"
    python scripts/deploy_contract.py

After it finishes, copy the printed contract address into CONTRACT_ADDRESS
(local .env and/or Render's environment variables).
"""
import os
import sys
import json

RPC_URL = os.environ.get('RPC_URL')
PRIVATE_KEY = os.environ.get('PRIVATE_KEY')

if not RPC_URL or not PRIVATE_KEY:
    print("ERROR: Set RPC_URL and PRIVATE_KEY environment variables before running this script.")
    print("  RPC_URL     - a Sepolia RPC endpoint (Infura/Alchemy)")
    print("  PRIVATE_KEY - a funded Sepolia testnet account's private key")
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(ROOT, 'contracts', 'BioShieldLedger.sol')
ABI_OUTPUT_PATH = os.path.join(ROOT, 'contract_abi.json')

from solcx import compile_source, install_solc, set_solc_version

SOLC_VERSION = '0.8.19'
print(f"Installing solc {SOLC_VERSION} (first run only, cached after)...")
install_solc(SOLC_VERSION)
set_solc_version(SOLC_VERSION)

with open(CONTRACT_PATH) as f:
    source = f.read()

print("Compiling BioShieldLedger.sol...")
compiled = compile_source(source, output_values=['abi', 'bin'])
contract_id, contract_interface = next(iter(compiled.items()))
abi = contract_interface['abi']
bytecode = contract_interface['bin']

from web3 import Web3

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    print(f"ERROR: could not connect to RPC_URL: {RPC_URL}")
    sys.exit(1)

account = w3.eth.account.from_key(PRIVATE_KEY)
print(f"Deploying from account: {account.address}")

balance = w3.eth.get_balance(account.address)
print(f"Account balance: {w3.from_wei(balance, 'ether')} SepoliaETH")
if balance == 0:
    print("WARNING: account has 0 balance - get free Sepolia testnet ETH from a faucet "
          "(e.g. https://sepoliafaucet.com) before this will succeed.")

Ledger = w3.eth.contract(abi=abi, bytecode=bytecode)
tx = Ledger.constructor().build_transaction({
    'from': account.address,
    'nonce': w3.eth.get_transaction_count(account.address),
    'gas': 1_000_000,
    'gasPrice': w3.eth.gas_price,
})
signed = account.sign_transaction(tx)
raw = getattr(signed, 'raw_transaction', None) or signed.rawTransaction
print("Sending deployment transaction...")
tx_hash = w3.eth.send_raw_transaction(raw)
print(f"Tx hash: {tx_hash.hex()} - waiting for confirmation...")
receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

contract_address = receipt.contractAddress
print(f"\nDeployed BioShieldLedger at: {contract_address}")
print(f"Block number: {receipt.blockNumber}")

with open(ABI_OUTPUT_PATH, 'w') as f:
    json.dump(abi, f, indent=2)
print(f"\nWrote ABI to {ABI_OUTPUT_PATH}")

print("\nNext step: set this environment variable (locally and on Render):")
print(f"  CONTRACT_ADDRESS={contract_address}")
