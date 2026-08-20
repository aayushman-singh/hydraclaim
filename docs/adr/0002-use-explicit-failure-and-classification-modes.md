# Use explicit failure and classification modes

HydraClaim stops dependent work when input, HydraDB, or language-model operations fail. It does not select a default value, use the current clock, retry, or change classification mode after a failure.

Classification uses an explicit `heuristic` or `llm` mode. Abstention remains a valid supported result because it states that stored claims do not support an answer.
