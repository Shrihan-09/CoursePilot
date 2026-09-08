# Router skills

Intent classification and skill selection. Given a student request, decide:

1. What is the student asking for? (course lookup, eligibility check,
   requirement question, semester plan, full schedule)
2. Which skills must be loaded to answer it?
3. Which tools/retrievers must run?
4. What is missing? (e.g. we do not know the student program yet)

NOT IMPLEMENTED YET. See docs/AGENT_ARCHITECTURE.md and docs/DECISION_TREE.md.

Design note: routing should return a *typed* decision object, not prose, so a
mis-route is a validation error rather than a silently odd answer.
