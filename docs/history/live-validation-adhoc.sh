#!/usr/bin/env bash
# Eight wording flips of dev scenarios; the results are recorded in docs/validation.md.
set -e
set -a; . ./.env; set +a

.venv/bin/python -m dispute_agent ask A-1001 'priya r says we hit her w/ a $25 late fee in sept but shes on autopay?? wants it gone' --quiet
.venv/bin/python -m dispute_agent ask A-1003 'cust says a rep told her early sept that autopay was still on. sept never drafted, late fee hit. wants it off' --quiet
.venv/bin/python -m dispute_agent ask A-1005 'mailed a check ~9/28 for oct, her bank says cashed, acct still shows oct unpaid + a late fee. whats going on' --quiet
.venv/bin/python -m dispute_agent ask A-1007 'Andre says he paid October by phone on the 5th for $455.30 and was given confirmation 48213. The account shows him late with a fee.' --quiet
.venv/bin/python -m dispute_agent ask A-1010 'Customer received our letter saying his September payment was returned, but his banking app showed it clearing on the 5th. He also asks about a $15 charge.' --quiet
.venv/bin/python -m dispute_agent ask A-1011 'oct pmt left her checking 10/1 then got reversed like a wk later?? now past due w/ a late fee. why' --quiet
.venv/bin/python -m dispute_agent ask A-1015 'Customer says the refund for her September double payment never reached her bank account ending 4471. It has been almost a month.' --quiet
.venv/bin/python -m dispute_agent ask A-1016 'Customer asks where his refund is. He was told "a couple of weeks" on a call and was not told the amount.' --quiet
