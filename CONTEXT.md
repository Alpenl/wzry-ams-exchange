# Experience Server Reward Exchange

This context redeems Honor of Kings experience-server rewards through the Tencent AMS activity and records whether the requested reward goals were satisfied.

## Rewards

**Reward**:
An item offered by the activity for a configured experience-voucher cost and identified by a Reward ID.
_Avoid_: Gift, package, prize

**Reward ID**:
The stable project-facing identifier used to select a Reward without exposing the activity's AMS flow identifier.
_Avoid_: Flow ID, index

**Exchange Plan**:
An ordered set of Rewards requested in one execution.
_Avoid_: Batch, loop

## Credentials

**Credential Bundle**:
The QQ login cookies and activity identity fields required to inspect or redeem Rewards for one account.
_Avoid_: Cookie dict, login data, account context

**Activity Identity**:
The experience-server OpenID, official-server OpenID, area, and partition derived from the activity information in a Credential Bundle.
_Avoid_: tyinfo, user info

## Outcomes

**Redemption Attempt**:
One request to satisfy one Reward in an Exchange Plan.
_Avoid_: Request, exchange call

**Redemption Outcome**:
The classified result of a Redemption Attempt, including newly redeemed, already satisfied, rejected, authentication failure, transient failure, or protocol failure.
_Avoid_: Result dict, response, status string

**Satisfied Outcome**:
A Redemption Outcome where the Reward was newly redeemed or had already been redeemed under the activity's daily or period limit.
_Avoid_: Success only

**Exchange Report**:
The ordered collection of Redemption Outcomes for an Exchange Plan and the authoritative statement of whether the plan was satisfied.
_Avoid_: CLI output, workflow status, summary dict

## Daily Automation

**Daily Run**:
The Exchange Plan intended to be satisfied for one Asia/Shanghai calendar date.
_Avoid_: Cron run, Action run, sign-in

**Daily Run Guard**:
The policy that observes Daily Runs and requests a compensating execution when the day's plan has no successful or active run.
_Avoid_: Watchdog script, second cron
