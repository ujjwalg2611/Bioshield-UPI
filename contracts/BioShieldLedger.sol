// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title BioShieldLedger
/// @notice Stores a hash of each BioShield-UPI transaction on-chain so a
///         record can later be proven untampered. Testnet-only - no real
///         funds ever touch this contract, it only notarizes a hash.
contract BioShieldLedger {
    struct Record {
        bytes32 recordHash;
        uint256 timestamp;
        bool exists;
    }

    mapping(string => Record) private records;
    address public owner;

    event TransactionNotarized(string indexed txnId, bytes32 recordHash, uint256 timestamp);

    constructor() {
        owner = msg.sender;
    }

    /// @notice Notarize a transaction hash. Each txnId can only be notarized once.
    function notarize(string calldata txnId, bytes32 recordHash) external {
        require(!records[txnId].exists, "Transaction already notarized");
        records[txnId] = Record({
            recordHash: recordHash,
            timestamp: block.timestamp,
            exists: true
        });
        emit TransactionNotarized(txnId, recordHash, block.timestamp);
    }

    /// @notice Look up a previously notarized record.
    function getRecord(string calldata txnId)
        external
        view
        returns (bytes32 recordHash, uint256 timestamp, bool exists)
    {
        Record memory r = records[txnId];
        return (r.recordHash, r.timestamp, r.exists);
    }
}
