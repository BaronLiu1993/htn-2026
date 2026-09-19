# 2025 Appetite Guidelines Reference

## What are appetite guidelines?

Appetite guidelines are a set of rules or criteria that describe the kinds of insurance submissions a carrier is interested in underwriting.

Examples:

- Only accept Commercial Auto policies in Texas.
- Target submissions with premium greater than $100,000.
- Avoid policies from high-risk ZIP codes.
- Prefer new business over renewals.

## Why they matter for the challenge

These guidelines represent how real-world underwriters make decisions every day. The agent should:

1. Use these rules to qualify submissions.
2. Explain to users (underwriters) why a submission is prioritized.
3. Help underwriters take faster, more confident action.

Guidelines equal underwriting strategy. The agent should make that strategy clear and actionable.

## How to use the guidelines

1. **Read the appetite document.** It is a simple set of rules, for example:

   - Accept if `product_type = "Commercial Property"` and `state = "NY"`.
   - Reject if `premium < $50,000`.
   - Prioritize if `submission_date` is within the last 7 days.

2. **Translate the rules into logic.** If a submission matches a rule, mark it “in appetite.” If not, mark it “out of appetite” or lower priority. Simple conditional logic plus an LLM call for the explanation is enough; heavy ML is not required.
3. **Apply the logic to the sample data** pulled from the API.
4. **Surface the results in the UI.** Show which submissions are in appetite, a short explanation of why, and optionally a score or visual signal (color, icon, or badge).

## 2025 sample: Commercial Property underwriting guidelines

| Factor | Acceptable | Target | Not acceptable |
| --- | --- | --- | --- |
| Submission type | New business | Renewal business | — |
| Line of business | Property | — | All other lines |
| Primary risk state | OH, PA, MD, CO, CA, FL, NC, SC, GA, VA, UT | OH, PA, MD, CO, CA, FL | All other states |
| TIV (Total Insured Value) | Up to $150M | $50M–$100M | Over $150M |
| Total premium | $50K–$175K | $75K–$100K | Under $50K or over $175K |
| Building age | Newer than 1990 | Newer than 2010 | Older than 1990 |
| Construction type | >50% JM, non-combustible/steel, or masonry non-combustible | >50% other types | — |
| Loss value | Under $100,000 | — | Over $100,000 |

Required data points behind these rules: account name, primary risk state, line of business, effective/expiration dates, TIV, construction type, building year, premium, and five-year loss history.
