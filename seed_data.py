from faker import Faker
import random
import asyncpg
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

load_dotenv()
faker = Faker()
start_date = datetime(2024, 1, 1)
end_date = datetime(2025, 7, 31)

def random_date():
    return faker.date_time_between(start_date=start_date, end_date=end_date)

async def seed_data():
    conn = await asyncpg.connect(
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        database=os.getenv("PGDATABASE"),
        host=os.getenv("PGHOST"),
        port=5432
    )


    # Delete data
    await conn.execute("DELETE FROM campaign_metrics")
    await conn.execute("DELETE FROM utm_tracking") 
    await conn.execute("DELETE FROM page_visits")
    await conn.execute("DELETE FROM activities")
    await conn.execute("DELETE FROM accounts")
    await conn.execute("DELETE FROM leads")
    await conn.execute("DELETE FROM campaigns")


    # Reset ID sequences
    await conn.execute("ALTER SEQUENCE leads_id_seq RESTART WITH 1")
    await conn.execute("ALTER SEQUENCE page_visits_id_seq RESTART WITH 1")
    await conn.execute("ALTER SEQUENCE accounts_id_seq RESTART WITH 1")
    await conn.execute("ALTER SEQUENCE activities_id_seq RESTART WITH 1")
    await conn.execute("ALTER SEQUENCE campaigns_id_seq RESTART WITH 1")
    await conn.execute("ALTER SEQUENCE campaign_metrics_id_seq RESTART WITH 1")


    # Leads
    leads = []
    for i in range(500):
        leads.append((
            faker.first_name(),
            faker.last_name(),
            faker.email(),
            faker.city(),
            faker.state(),
            random_date().date()
        ))
    await conn.executemany(
        "INSERT INTO leads (first_name, last_name, email, city, state, created_at) VALUES ($1, $2, $3, $4, $5, $6)", leads
    )

    # Page visits
    pages = ['/home', '/pricing', '/contact', '/blog', '/about']
    lead_ids = list(range(1, 501))
    session_ids = [faker.uuid4() for _ in range(2000)]
    visits = []
    for _ in range(4000):
        visits.append((
            random.choice(lead_ids),
            random.choice(session_ids),
            random.choice(pages),
            random_date()
        ))
    await conn.executemany(
        "INSERT INTO page_visits (lead_id, session_id, page_path, visit_date) VALUES ($1, $2, $3, $4)", visits
    )

    # Accounts
    tiers = ['enterprise', 'midmarket', 'smallbiz']
    accounts = []
    for _ in range(1000):
        accounts.append((
            faker.company(),
            random.choice(tiers),
            random.randint(1000, 200000),
            faker.name()
        ))
    await conn.executemany(
        "INSERT INTO accounts (name, tier, arr, assigned_rep) VALUES ($1, $2, $3, $4)", accounts
    )

    # Activities
    account_ids = list(range(1, 1001))
    # Fetch the list of reps that were used when creating accounts
    rep_names = [a[3] for a in accounts]  # index 3 is assigned_rep

    channels = ['LinkedIn', 'Email', 'In-Person', 'Phone Call', 'SMS']
    activity_types = ['call', 'email', 'message', 'meeting']

    activities = []
    for _ in range(4000):
        rep = random.choice(rep_names)
        activities.append((
            random.choice(account_ids),
            random.choice(activity_types),
            random.choice(channels),
            rep,
            random_date()
        ))

    await conn.executemany(
        """
        INSERT INTO activities (account_id, activity_type, channel, rep, created_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        activities
    )

    # Campaigns
    campaign_types = ['email', 'webinar', 'demo_request']
    campaigns = []
    for _ in range(100):
        name = faker.catch_phrase()
        start = random_date()
        end = start + timedelta(days=random.randint(7, 30))
        campaigns.append((
            name,
            random.choice(campaign_types),
            start,
            end
        ))
    await conn.executemany(
        "INSERT INTO campaigns (name, type, start_date, end_date) VALUES ($1, $2, $3, $4)", campaigns
    )


    # Campaign Metrics
    campaign_ids = list(range(1, 101))  # Assuming 100 campaigns were inserted
    metrics = []
    for cid in campaign_ids:
        sent = random.randint(500, 10000)
        clicks = random.randint(0, sent)
        conversions = random.randint(0, clicks)
        deal_size = conversions * random.randint(500, 5000)  # simulate revenue impact
        metrics.append((
            cid,
            sent,
            clicks,
            conversions,
            deal_size
        ))

    await conn.executemany(
        """
        INSERT INTO campaign_metrics (campaign_id, sent, clicks, conversions, deal_size)
        VALUES ($1, $2, $3, $4, $5)
        """,
        metrics
    )

    
    # utm_tracking
    sources = ['organic', 'paid_search', 'social', 'email', 'referral']
    mediums = ['cpc', 'social', 'email', 'banner']
    campaign_ids = list(range(1, 101))
    utms = []
    for s in session_ids:
        utms.append((
            s,
            random.choice(sources),
            random.choice(mediums),
            random.choice(campaign_ids)
        ))

    await conn.executemany(
        "INSERT INTO utm_tracking (session_id, source, medium, campaign_id) VALUES ($1, $2, $3, $4)", utms
    )

    await conn.close()

asyncio.run(seed_data())
