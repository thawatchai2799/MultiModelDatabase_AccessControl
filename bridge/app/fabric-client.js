// Fabric Gateway connection, adapted directly from fabric/bench/bench.mjs
// (the script that was already run successfully against a real Fabric
// test-network in our earlier intrusion-detection work). Same
// identity path convention, same gRPC/TLS setup, same deadlines. The only
// changes are: parameterised via env vars instead of hardcoded, and it
// submits AccessLedger transactions instead of CityAnchor ones.
import * as grpc from '@grpc/grpc-js';
import { connect, signers } from '@hyperledger/fabric-gateway';
import * as crypto from 'crypto';
import { promises as fs } from 'fs';
import * as path from 'path';

const CHANNEL = process.env.FABRIC_CHANNEL || 'mychannel';
const CHAINCODE = process.env.FABRIC_CHAINCODE || 'accessledger';
const ROOT = process.env.FABRIC_SAMPLES || '/fabric-samples';
// The bridge container reaches the peer's published port on the host via
// host.docker.internal (see docker-compose.yml); network-up.sh publishes
// peer0.org1 at the test-network's usual localhost:7051.
const PEER_ENDPOINT = process.env.FABRIC_PEER_ENDPOINT || 'host.docker.internal:7051';
const PEER_HOSTNAME_OVERRIDE = process.env.FABRIC_PEER_HOSTNAME || 'peer0.org1.example.com';

let gatewayPromise = null;

async function buildGateway() {
  const org = path.join(ROOT, 'test-network/organizations/peerOrganizations/org1.example.com');
  const certPath = path.join(org, 'users/User1@org1.example.com/msp/signcerts');
  const keyPath = path.join(org, 'users/User1@org1.example.com/msp/keystore');
  const tls = await fs.readFile(path.join(org, 'peers/peer0.org1.example.com/tls/ca.crt'));

  const client = new grpc.Client(
    PEER_ENDPOINT,
    grpc.credentials.createSsl(tls),
    { 'grpc.ssl_target_name_override': PEER_HOSTNAME_OVERRIDE }
  );
  const cert = await fs.readFile(path.join(certPath, (await fs.readdir(certPath))[0]));
  const key = crypto.createPrivateKey(await fs.readFile(path.join(keyPath, (await fs.readdir(keyPath))[0])));

  const gateway = connect({
    client,
    identity: { mspId: 'Org1MSP', credentials: cert },
    signer: signers.newPrivateKeySigner(key),
    evaluateOptions: () => ({ deadline: Date.now() + 15000 }),
    endorseOptions: () => ({ deadline: Date.now() + 15000 }),
    submitOptions: () => ({ deadline: Date.now() + 15000 }),
    // Committing (waiting for the transaction to land in a block) is the
    // slow step; a generous deadline here matters more for correctness than
    // for the timing metric, since AnchorAccessEvent's own commit latency is
    // deliberately reported as a *separate* number (see server.js) rather
    // than folded into the Leak Window measurement itself.
    commitStatusOptions: () => ({ deadline: Date.now() + 60000 }),
  });
  return { gateway, client };
}

// Lazily connect once, reuse the same gateway for the life of the process.
// A fresh connection per request would add its own latency noise to every
// measurement, which is exactly the kind of environment-induced noise this
// project has been careful to keep out of the metrics.
async function getContract() {
  if (!gatewayPromise) gatewayPromise = buildGateway();
  const { gateway } = await gatewayPromise;
  return gateway.getNetwork(CHANNEL).getContract(CHAINCODE);
}

export async function anchorAccessEvent(eventId, resourceId, principalId, action, ts) {
  const contract = await getContract();
  const result = await contract.submitTransaction(
    'AnchorAccessEvent', eventId, resourceId, principalId, action, String(ts)
  );
  return JSON.parse(Buffer.from(result).toString('utf8'));
}

export async function recordPropagation(eventId, layer, ts) {
  const contract = await getContract();
  const result = await contract.submitTransaction('RecordPropagation', eventId, layer, String(ts));
  return JSON.parse(Buffer.from(result).toString('utf8'));
}

export async function isContained(resourceId, principalId) {
  const contract = await getContract();
  const result = await contract.evaluateTransaction('IsContained', resourceId, principalId);
  return JSON.parse(Buffer.from(result).toString('utf8'));
}

export async function getAccessHistory(resourceId) {
  const contract = await getContract();
  const result = await contract.evaluateTransaction('GetAccessHistory', resourceId);
  return JSON.parse(Buffer.from(result).toString('utf8'));
}

export async function closeFabric() {
  if (!gatewayPromise) return;
  const { gateway, client } = await gatewayPromise;
  gateway.close();
  client.close();
  gatewayPromise = null;
}
