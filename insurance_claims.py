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
    coverage_amount: u256
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
    coverage_pct: u256
    payout: u256
    fraud_detected: bool
    ai_reasoning: str
    timestamp: str


class InsuranceClaims(gl.Contract):
    policies: TreeMap[str, str]
    claims: TreeMap[str, str]
    claim_count: u256
    policy_count: u256
    fraudsters: TreeMap[str, str]

    def __init__(self):
        pass

    def _adjudicate_claim(self, coverage_type: str, incident_date: str, description: str, evidence_urls: str) -> dict:
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
You are an insurance claims adjudicator. Evaluate a claim against coverage and check for fraud.

COVERAGE TYPE: {coverage_type}
INCIDENT DATE: {incident_date}
INCIDENT DESCRIPTION: {description}

EVIDENCE:
{chr(10).join(evidence_texts) if evidence_texts else "[none submitted]"}

Respond ONLY in this JSON format with these exact fields:
{{
    "decision": "APPROVE" | "DENY" | "PARTIAL",
    "coverage_percentage": int,  // 0-100, % of coverage paid. 0 if denied, partial between 1-99, full 100
    "fraud_detected": bool,  // true only if evidence clearly indicates fraud
    "reasoning": str
}}
"""
            result = gl.exec_prompt(task).replace("```json", "").replace("```", "")
            return json.dumps(json.loads(result), sort_keys=True)

        principle = "Validators must agree on ALL THREE core outputs: the decision label (APPROVE/DENY/PARTIAL), the exact coverage_percentage (0-100), and the fraud_detected boolean. Reasoning wording may differ."
        result_json = json.loads(gl.eq_principle_prompt_comparative(gather_and_adjudicate, principle))
        return result_json

    @gl.public.write
    def create_policy(self, coverage_type: str, coverage_amount: u256, deductible: u256, start_ts: str, end_ts: str):
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
            decision="", coverage_pct=0, payout=0, fraud_detected=False,
            ai_reasoning="", timestamp=str(gl.message.timestamp),
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
            policy["coverage_type"], claim["incident_date"],
            claim["incident_description"], claim["evidence_urls"],
        )

        decision = result.get("decision", "DENY")
        pct = result.get("coverage_percentage", 0)
        fraud = bool(result.get("fraud_detected", False))

        if decision not in ("APPROVE", "DENY", "PARTIAL"):
            decision = "DENY"
            pct = 0
        if pct < 0: pct = 0
        if pct > 100: pct = 100
        if decision == "DENY": pct = 0

        coverage = policy["coverage_amount"]
        gross = coverage * pct // 100
        deductible = policy["deductible"]
        payout = max(0, gross - deductible) if gross > deductible else 0

        claim["status"] = "RESOLVED"
        claim["decision"] = decision
        claim["coverage_pct"] = pct
        claim["payout"] = payout
        claim["fraud_detected"] = fraud
        claim["ai_reasoning"] = result.get("reasoning", "")
        self.claims[claim_id] = json.dumps(claim)

        if fraud:
            self.fraudsters[claim["holder"]] = json.dumps(result.get("reasoning", ""))

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
        fraud_count = 0
        for v in self.claims.values():
            c = json.loads(v)
            if c["status"] == "PENDING": pending += 1
            elif c["status"] == "RESOLVED":
                if c["decision"] == "APPROVE": approved += 1
                elif c["decision"] == "DENY": denied += 1
                elif c["decision"] == "PARTIAL": partial += 1
                total_payout += c["payout"]
                if c["fraud_detected"]: fraud_count += 1
        return {
            "policies": len(self.policies), "claims": len(self.claims),
            "pending": pending, "approved": approved, "denied": denied,
            "partial": partial, "total_payout": total_payout,
            "fraud_detected": fraud_count, "fraudsters": len(self.fraudsters),
        }
