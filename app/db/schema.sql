CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- People
CREATE TABLE IF NOT EXISTS crews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT, skills TEXT[],
  base_lat NUMERIC, base_lng NUMERIC, daily_hours NUMERIC DEFAULT 8
);
CREATE TABLE IF NOT EXISTS staff_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT, wa_phone TEXT UNIQUE,
  role TEXT CHECK (role IN ('director','manager','sales','crew_lead','technician','admin')),
  on_duty BOOLEAN DEFAULT false, crew_id UUID REFERENCES crews, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS contacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), full_name TEXT, phone TEXT, email TEXT,
  address TEXT, lat NUMERIC, lng NUMERIC,
  type TEXT DEFAULT 'lead' CHECK (type IN ('lead','customer','website_client')),
  first_source TEXT, first_campaign_id UUID, language TEXT, consent_marketing BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS channel_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), contact_id UUID REFERENCES contacts,
  channel TEXT CHECK (channel IN ('whatsapp','messenger','fb_comment')),
  external_id TEXT, UNIQUE (channel, external_id)
);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), contact_id UUID REFERENCES contacts,
  channel TEXT, wa_number_id TEXT,
  state TEXT DEFAULT 'ai_active' CHECK (state IN ('ai_active','awaiting_client','awaiting_director','human_takeover','closed')),
  assigned_staff_id UUID REFERENCES staff_users,
  last_inbound_at TIMESTAMPTZ, summary TEXT, updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (contact_id, channel)
);
CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id UUID REFERENCES conversations,
  direction TEXT CHECK (direction IN ('in','out')),
  sender TEXT CHECK (sender IN ('contact','ai','staff','system')),
  external_message_id TEXT UNIQUE, type TEXT, body TEXT, media_url TEXT,
  intent TEXT, ai_confidence NUMERIC, status TEXT, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_conv_idx ON messages (conversation_id, created_at);
CREATE TABLE IF NOT EXISTS escalations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id UUID REFERENCES conversations,
  kind TEXT CHECK (kind IN ('call_request','quote_review','complaint','website_feature','other')),
  reason TEXT, priority INT, assigned_to UUID REFERENCES staff_users,
  status TEXT DEFAULT 'open' CHECK (status IN ('open','claimed','resolved')),
  sla_due_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now(), resolved_at TIMESTAMPTZ
);

-- Sales
CREATE TABLE IF NOT EXISTS products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), sku TEXT UNIQUE, name TEXT, category TEXT,
  specs JSONB, supplier TEXT, active BOOLEAN DEFAULT true
);
CREATE TABLE IF NOT EXISTS stock_levels (
  product_id UUID PRIMARY KEY REFERENCES products,
  qty_on_hand INT, qty_reserved INT DEFAULT 0, unit_cost NUMERIC, unit_price NUMERIC,
  currency TEXT DEFAULT 'USD', lead_time_days INT, updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS price_history (
  id BIGSERIAL PRIMARY KEY, product_id UUID REFERENCES products,
  unit_cost NUMERIC, unit_price NUMERIC, source TEXT, recorded_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), contact_id UUID REFERENCES contacts,
  conversation_id UUID REFERENCES conversations, version INT DEFAULT 1, spec JSONB,
  subtotal NUMERIC, labour NUMERIC, transport NUMERIC, margin_pct NUMERIC, total NUMERIC,
  currency TEXT DEFAULT 'USD', confidence NUMERIC, flags TEXT[],
  status TEXT DEFAULT 'draft' CHECK (status IN ('draft','pending_director','approved','sent','accepted','rejected','expired')),
  approved_by UUID REFERENCES staff_users, valid_until DATE, pdf_url TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS quote_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), quote_id UUID REFERENCES quotes,
  product_id UUID REFERENCES products, qty NUMERIC, unit_price_snapshot NUMERIC,
  line_total NUMERIC, in_stock BOOLEAN
);

-- Operations
CREATE TABLE IF NOT EXISTS jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), ref TEXT UNIQUE, contact_id UUID REFERENCES contacts,
  quote_id UUID REFERENCES quotes, source_channel TEXT, type TEXT, size_kw NUMERIC,
  est_hours NUMERIC, required_skills TEXT[], address TEXT, lat NUMERIC, lng NUMERIC,
  client_windows JSONB,
  status TEXT DEFAULT 'new' CHECK (status IN ('new','awaiting_schedule','scheduled','in_progress','completed','on_hold','cancelled')),
  priority INT DEFAULT 0, notes TEXT, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status);
CREATE TABLE IF NOT EXISTS schedule_slots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), job_id UUID REFERENCES jobs, crew_id UUID REFERENCES crews,
  starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, travel_minutes INT,
  status TEXT DEFAULT 'proposed' CHECK (status IN ('proposed','client_confirmed','team_notified','done','moved')),
  created_by TEXT
);
CREATE INDEX IF NOT EXISTS slots_crew_idx ON schedule_slots (crew_id, starts_at);

-- Marketing & AI
CREATE TABLE IF NOT EXISTS campaigns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), requested_by UUID REFERENCES staff_users,
  service_type TEXT, brief TEXT, geo JSONB, start_at TIMESTAMPTZ, end_at TIMESTAMPTZ,
  budget NUMERIC, budget_type TEXT, meta_campaign_id TEXT, meta_adset_id TEXT, meta_ad_id TEXT,
  status TEXT DEFAULT 'collecting' CHECK (status IN ('collecting','generated','awaiting_approval','in_review','active','paused','ended','rejected')),
  metrics JSONB, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ad_creatives (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), campaign_id UUID REFERENCES campaigns,
  headline TEXT, primary_text TEXT, image_url TEXT, template_id TEXT, selected BOOLEAN DEFAULT false
);
CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), source_type TEXT, source_ref TEXT,
  service_type TEXT, performance_score NUMERIC, content TEXT, embedding VECTOR(1024),
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS knowledge_embedding_idx ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- Websites
CREATE TABLE IF NOT EXISTS sites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), client_contact_id UUID REFERENCES contacts,
  domain TEXT, template TEXT, content JSONB, published_version INT DEFAULT 0, deploy_hook_url TEXT
);
CREATE TABLE IF NOT EXISTS site_change_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(), site_id UUID REFERENCES sites,
  message_id UUID REFERENCES messages,
  kind TEXT CHECK (kind IN ('content','feature','unclear')), diff JSONB, preview_url TEXT, version INT,
  status TEXT DEFAULT 'drafted' CHECK (status IN ('drafted','previewed','approved','published','rolled_back','ticketed')),
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGSERIAL PRIMARY KEY, actor TEXT, action TEXT, entity TEXT, entity_id UUID,
  before JSONB, after JSONB, created_at TIMESTAMPTZ DEFAULT now()
);
