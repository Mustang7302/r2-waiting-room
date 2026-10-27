# R2 Waiting Room

Watches a Rivian R2 order for the hidden signals that come before production and delivery, and tells you the moment one moves.

Rivian's garage page shows you a checklist of things *you* have to do. It does not show you when *Rivian* does something: when your configuration gets hard-locked, when a build gets matched to your order, when a VIN lands, when the purchase steps unlock. Those fields are in the data behind the page. R2 Waiting Room polls them every 4 hours, keeps a history, and pushes an alert to your phone when anything changes.

It runs entirely on GitHub, for free, with nothing to install:

- **GitHub Actions** polls Rivian on a schedule.
- **GitHub Pages** serves a phone-friendly dashboard with a 9-rung "fulfillment ladder".
- **ntfy** (a free push-notification app) delivers alerts.

Your personal details never appear in the repo in plain text. See [Privacy](#privacy-in-plain-terms).

> Unofficial. Not affiliated with Rivian. It uses the same read-only API calls the Rivian app makes, but Rivian's terms do not cover tools like this. Use at your own discretion.

---

## Setup guide (about 20 minutes, browser only)

**What you need:** a free GitHub account, your Rivian login, and a phone with the free **ntfy** app (App Store or Google Play).

1. **Copy the template.** On this repo's page click the green **Use this template** button, then **Create a new repository**. Name it `r2-waiting-room`, leave it **Public** (the free dashboard needs that; your personal details are never published), and click **Create repository**.

2. **Turn on the dashboard.** In your new repo click **Settings**, then **Pages** in the left menu. Under "Build and deployment" set Source to **Deploy from a branch**, Branch to **main**, folder to **/docs**, and click **Save**. After a minute GitHub shows your dashboard address at the top of that page. Write it down.

3. **Set up alerts.** Open the ntfy app, tap **+** and subscribe to a topic name nobody would guess, such as `r2wr-` followed by 8 random letters and numbers. That name is your private alert channel.

4. **Make a secret key.** You need one long random string; it encrypts your Rivian session inside the repo. Use a password manager's generator, or any site that generates a 40-character random password. Copy it. You will paste it once and never need it again.

5. **Add your secrets.** In the repo click **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add four, names exactly as shown:

   | Name | Value |
   | --- | --- |
   | `RIVIAN_EMAIL` | your Rivian account email |
   | `RIVIAN_PASSWORD` | your Rivian password |
   | `SESSION_KEY` | the random string from step 4 |
   | `NTFY_TOPIC` | the topic name from step 3 |

   Use **Repository secrets** (the green "New repository secret" button), not Environment secrets; the workflows only read repository secrets. GitHub stores them encrypted; nobody, including you, can read them back.

6. **Enable the automations.** Click the **Actions** tab. If GitHub shows "I understand my workflows, go ahead and enable them", click it.

7. **Sign in to Rivian, part 1.** In Actions, click **Login to Rivian** in the left list, then **Run workflow** on the right. Leave step as `start` and click the green **Run workflow**. Wait for the green check (about a minute). Rivian texts or emails you a 6-digit code, the same as when you sign in to the app.

8. **Sign in to Rivian, part 2.** Within 5 minutes, click **Run workflow** again, change step to `finish`, type the 6-digit code in the code box, and run it. Green check means you are signed in. Your password was used once and is stored nowhere except the GitHub secret you control.

9. **First poll.** The `finish` step runs a poll for you. Open your dashboard address from step 2. You should see your R2 and the rung you are on. (If you want to see the dashboard before signing in, run **Poll Rivian** with "Use the sample order" ticked.)

10. **Done.** It now checks every 4 hours on its own. Every check sends a quiet "Checked, no change" push with the time and your current rung (turn that off with the **Settings** workflow under Actions, or the "Check-in pushes" switch at the bottom of the dashboard, which opens that workflow). When something moves you get a loud push and the dashboard updates. About once a week Rivian expires the session; you get a red push and an email from GitHub, and you repeat steps 7 and 8.

### If something goes wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| Red X on "Poll Rivian" and a push saying session expired | Rivian's weekly session expiry | Repeat steps 7 and 8 |
| Red X on "Login to Rivian" finish | The code expired or was mistyped | Run `start` again, then `finish` within 5 minutes |
| Dashboard says "No data yet" | Pages is on but no poll has run, or the first poll failed | Run "Poll Rivian" and check for a green check |
| Dashboard address shows 404 | Pages not enabled, or still building | Check step 2; wait 2 minutes and reload |
| Yellow banner in Actions saying scheduled workflows were disabled | GitHub pauses schedules after 60 days of inactivity | Click "Enable workflow" |
| Polls are green but no push alerts | Topic mismatch | Compare the `NTFY_TOPIC` secret with the topic in the app |
| Yellow banner on the dashboard listing failed queries | Rivian changed or rejected one query; the rest still work | Open an issue with the banner text |

### Privacy, in plain terms

The dashboard and the repo are public, but the only things ever written in plain text are order status words, dates, your model and options, your VIN with its last 3 characters hidden, and delivery appointment times. Your name, address, phone, email, payments, and the full raw data are either never written or written encrypted with your secret key (`state/*.enc`). The sanitizer is an allowlist (`app/sanitize.py`): anything not explicitly listed is dropped, and the poller refuses to publish a file that contains an address, email, or payment field.

Want the full VIN on your dashboard? Run the **Settings** workflow under Actions and choose "show".

---

## What it watches

The **fulfillment ladder** has nine rungs. Each is mapped to raw Rivian fields; the dashboard shows the raw value under every rung when "Show field names" is on.

| Rung | Reached when |
| --- | --- |
| 1 Preordered | `state == ORDERED` |
| 2 Invited to order | `invitedToSelfConfigure` or `invitedToSelfConfirm` is true, or implied by a placed order (shown as "Complete" with no date) |
| 3 Ordered | `configurationStatus` is set (SOFT_LOCKED after you lock your build and sign the agreement) |
| 4 In the queue | `fulfillmentSummaryStatus == PENDING_FULFILLMENT` |
| 5 Build matched | `vehicleId` appears, or `configurationStatus` is anything other than SOFT_LOCKED, or a `plannedBuildDate` appears |
| 6 VIN assigned | `vin` appears, or `fulfillmentSummaryStatus == READY_FOR_FULFILLMENT` |
| 7 Purchase opens | `isPreparingPurchase` becomes true, or the Title and reg step unlocks |
| 8 Delivery set | `delivery.status` is APPT_SCHEDULED or DELIVERY_ACCEPTED, or an appointment exists |
| 9 Delivered | `fulfillmentSummaryStatus == VEHICLE_DELIVERED` |

Rungs 5 to 7 follow the order R1 owners reported on the forums; R2 may differ, so a rung reached out of order is shown as reached, with its date, rather than forced into sequence.

Beyond the ladder, **every leaf in every response is diffed** against the previous poll. Unknown fields that move still show up in "What moved" and in your push. A short noise list (`offersByStore`, image URLs, feature flags) is stored but not alerted; edit it in `settings.json`.

## How it works

```
.github/workflows/poll.yml    every 4 h: decrypt session -> 5 read-only queries -> diff -> ladder -> publish -> push
.github/workflows/login.yml   manual, two steps: start (code sent) / finish (code -> session)
app/rivian/                   GraphQL client and documents (gateway, orders, t2d endpoints)
app/diff.py                   full-tree diff, keyed arrays, noise list
app/ladder.py                 the nine rung rules
app/sanitize.py               the public allowlist
app/poller.py, app/login.py   the two entry points
state/*.enc                   AES-256-GCM, key derived from SESSION_KEY: session, pending OTP, previous raw snapshot
docs/index.html               the dashboard (static, no build step)
docs/data/*.json              latest.json, rungs.json, changes.json, meta.json (sanitized)
fixtures/                     redacted sample payloads: pending_fulfillment (real shape), build_matched, vin_assigned (synthetic)
```

Queries, all read-only: `vehicleOrders`, `delivery` and `getUserInfo` on the gateway, `order` on the orders endpoint, and `transactionStatus` on the t2d endpoint (with a gateway fallback). The `order` and `transactionStatus` selections ask for every field the garage page shows; if Rivian's schema rejects some, the client drops just those fields and retries, so an unknown field never costs the poll. Nothing here sends a command or writes to Rivian.

## Running locally

```
pip install -r requirements.txt
python -m pytest                              # 23 tests, no network
python -m app.poller --mock                   # writes docs/data from the sample order
python -m app.poller --mock --fixture order.build_matched
python -m http.server -d docs 8000            # open http://localhost:8000
```

Live polling locally works the same way as in Actions: set `SESSION_KEY`, `RIVIAN_EMAIL`, `RIVIAN_PASSWORD`, `NTFY_TOPIC`, run `python -m app.login start`, then `python -m app.login finish --code 123456`, then `python -m app.poller`.

## Credits

Built on the community's reverse-engineering of Rivian's app API: [RivDocs](https://rivian-api.kaedenb.org/), [the-mace/rivian-python-api](https://github.com/the-mace/rivian-python-api) (MIT), and years of Rivian Forums threads. The look is inspired by Rivian's outdoors palette; no Rivian logos, wordmarks, or assets are used.

MIT licensed.
