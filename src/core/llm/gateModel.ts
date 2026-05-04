import type { GateDecision } from "../../shared/contracts/chat";
import { evaluateGate, type GateEvaluationInput } from "../orchestration/gateEvaluator";

export interface GateModel {
  evaluate(input: GateEvaluationInput): Promise<GateDecision>;
}

export class DeterministicGateModel implements GateModel {
  async evaluate(input: GateEvaluationInput): Promise<GateDecision> {
    return evaluateGate(input);
  }
}
