# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
from dataclasses import dataclass
from genlayer import *


@allow_storage
@dataclass
class Policy:
    policy_id: str
    holder: str
    coverage_type: str
    coverage_amount: str
    deductible: u256
    start_ts: str
    end_ts: str
    status: str


@allow_storage
@dataclass
class Claim:
    claim_id: str
    policy_id: str
    holder: str
    incident_date: str
    incident_description: str
    evidence_urls: str
    status: str
    decision: str
    payout: u256
    ai_reasoning: str
    fraud_flags: str
    timestamp: str


class InsuranceClaims(gl.Contract):
    policies: TreeMap[str, str]
    claims: TreeMap[str, str]
    claim_count: u256
    policy_count: u256
    fraudsters: TreeMap[str, str]

    def __init__(self):
        pass

    def _adjudicate_claim(self, coverage_type: str, coverage_amount: str, deductible: u256, incident_date: str, description: str, evidence_urls: str) -> dict:
        def gather_and_adjudicate() -> str:
            def fetch(urls_json: str) -> list:
                texts = []
                for url in json.loads(urls_json):
                    try:
                        content = gl.get_webpage(url, mode="text")
                        texts.append(f"[{url}]\n{content[:2500]}")
                    except Exception:
                        texts.append(f"[{url}] [FETCH_FAILED]")
                return texts

            evidence_texts = fetch(evidence_urls)

            task = f"""
You are an insurance claims adjudicator. Verify a claim against policy coverage, check for fraud, and determine payout.

COVERAGE TYPE: {coverage_type}
COVERAGE AMOUNT: {coverage_amount}
DEDUCTIBLE: {deductible}

INCIDENT DATE: {incident_date}
INCIDENT DESCRIPTION: {description}

EVIDENCE:
{chr(10).join(evidence_texts) if evidence_texts else "[none submitted]"}

Evaluate: (1) Is the incident within coverage? (2) Is the claim consistent across evidence? (3) Any fraud indicators? (4) Fair payout after deductible.

Respond ONLY in this JSON format:
{{
    "decision": str,  // "APPROVE", "DENY", or "PARTIAL"
    "payout": int,  // payout amount (0 if denied)
    "reasoning": str,
    "fraud_flags": [str]
}}
"""
            result = gl.exec_prompt(task).replace("```json", "").replace("```", "")
            return json.dumps(json.loads(result), sort_keys=True)

        principle = "Validators must agree on the core decision (APPROVE/DENY/PARTIAL). Minor differences in payout or fraud flag wording are acceptable if the core outcome matches."
        result_json = json.loads(gl.eq_principle_prompt_comparative(gather_and_adjudicate, principle))
        return result_json

    @gl.public.write
    def create_policy(self, coverage_type: str, coverage_amount: str, deductible: u256, start_ts: str, end_ts: str):
        sender = gl.message.sender_address
        self.policy_count += 1
        policy_id = str(self.policy_count)

        policy = Policy(
            policy_id=policy_id, holder=sender.as_hex,
            coverage_type=coverage_type, coverage_amount=coverage_amount,
            deductible=deductible, start_ts=start_ts, end_ts=end_ts,
            status="ACTIVE",
        )
        self.policies[policy_id] = json.dumps(policy.__dict__)

    @gl.public.write
    def file_claim(self, policy_id: str, incident_date: str, description: str, evidence_urls_json: str):
        sender = gl.message.sender_address
        policy = json.loads(self.policies.get(policy_id, "{}"))
        if not policy:
            raise Exception("Policy not found")
        if policy["holder"] != sender.as_hex:
            raise Exception("Only policy holder can file claims")
        if policy["status"] != "ACTIVE":
            raise Exception("Policy not active")

        self.claim_count += 1
        claim_id = str(self.claim_count)

        claim = Claim(
            claim_id=claim_id, policy_id=policy_id, holder=sender.as_hex,
            incident_date=incident_date, incident_description=description,
            evidence_urls=evidence_urls_json, status="PENDING",
            decision="", payout=0, ai_reasoning="", fraud_flags="[]",
            timestamp=str(gl.message.timestamp),
        )
        self.claims[claim_id] = json.dumps(claim.__dict__)

    @gl.public.write
    def process_claim(self, claim_id: str):
        claim = json.loads(self.claims.get(claim_id, "{}"))
        if not claim:
            raise Exception("Claim not found")
        if claim["status"] != "PENDING":
            raise Exception("Claim already processed")

        policy = json.loads(self.policies.get(claim["policy_id"], "{}"))
        if not policy:
            raise Exception("Policy not found")

        claim["status"] = "PROCESSING"
        self.claims[claim_id] = json.dumps(claim)

        result = self._adjudicate_claim(
            policy["coverage_type"], policy["coverage_amount"],
            policy["deductible"], claim["incident_date"],
            claim["incident_description"], claim["evidence_urls"],
        )

        claim["status"] = "RESOLVED"
        claim["decision"] = result["decision"]
        claim["payout"] = max(0, result["payout"] - policy["deductible"])
        claim["ai_reasoning"] = result["reasoning"]
        claim["fraud_flags"] = json.dumps(result["fraud_flags"])
        self.claims[claim_id] = json.dumps(claim)

        if result["fraud_flags"]:
            self.fraudsters[claim["holder"]] = json.dumps(result["fraud_flags"])

    @gl.public.view
    def get_claim(self, claim_id: str) -> str:
        return self.claims.get(claim_id, "{}")

    @gl.public.view
    def get_policy(self, policy_id: str) -> str:
        return self.policies.get(policy_id, "{}")

    @gl.public.view
    def is_fraudster(self, user: str) -> str:
        return self.fraudsters.get(user, "{}")

    @gl.public.view
    def get_stats(self) -> dict:
        approved = denied = partial = pending = 0
        total_payout = 0
        for v in self.claims.values():
            c = json.loads(v)
            if c["status"] == "PENDING": pending += 1
            elif c["status"] == "RESOLVED":
                if c["decision"] == "APPROVE": approved += 1
                elif c["decision"] == "DENY": denied += 1
                elif c["decision"] == "PARTIAL": partial += 1
                total_payout += c["payout"]
        return {
            "policies": len(self.policies),
            "claims": len(self.claims),
            "pending": pending, "approved": approved,
            "denied": denied, "partial": partial,
            "total_payout": total_payout,
            "fraudsters": len(self.fraudsters),
        }
