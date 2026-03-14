-- Open Brain PostgreSQL + pgvector schema
-- Embedding dim: 768 (nomic-embed-text)

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Core OB1 thoughts ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS thoughts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    content TEXT NOT NULL,
    embedding vector(768),
    metadata JSONB DEFAULT '{}',
    source TEXT DEFAULT 'telegram',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS thoughts_embedding_idx
    ON thoughts USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS thoughts_metadata_idx
    ON thoughts USING gin (metadata);
CREATE INDEX IF NOT EXISTS thoughts_created_idx
    ON thoughts (created_at DESC);

-- ── Family ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS family_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    role TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Unified calendar: event_type distinguishes production/appointment/family/etc.
CREATE TABLE IF NOT EXISTS calendar_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    description TEXT,
    event_type TEXT NOT NULL DEFAULT 'family',
    -- event_type: production | appointment | family | important_date |
    --             reorder_reminder | market_event | business
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    all_day BOOLEAN DEFAULT FALSE,
    family_member_id UUID REFERENCES family_members(id) ON DELETE SET NULL,
    recurrence_rule TEXT,       -- iCal RRULE or NULL
    outlook_event_id TEXT,      -- tracks Outlook sync → Skylight
    location TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS calendar_events_start_idx
    ON calendar_events (start_time);
CREATE INDEX IF NOT EXISTS calendar_events_type_idx
    ON calendar_events (event_type);
CREATE INDEX IF NOT EXISTS calendar_events_member_idx
    ON calendar_events (family_member_id);

-- ── Household knowledge base ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS household_knowledge (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    category TEXT NOT NULL,
    -- category: appliance | utility | wifi | emergency | home_fact |
    --           document | production_space
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    embedding vector(768),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS household_embedding_idx
    ON household_knowledge USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS household_category_idx
    ON household_knowledge (category);

-- ── Summit Pine inventory ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS inventory_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    -- category: raw_material | finished_good | packaging | equipment
    unit TEXT NOT NULL,
    quantity_on_hand DECIMAL(10,3) NOT NULL DEFAULT 0,
    reorder_threshold DECIMAL(10,3),
    reorder_quantity DECIMAL(10,3),
    unit_cost DECIMAL(10,4),
    supplier TEXT,
    supplier_lead_days INTEGER,
    is_critical BOOLEAN DEFAULT FALSE,
    notes TEXT,
    embedding vector(768),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS inventory_sku_idx     ON inventory_items (sku);
CREATE INDEX IF NOT EXISTS inventory_category_idx ON inventory_items (category);
CREATE INDEX IF NOT EXISTS inventory_critical_idx ON inventory_items (is_critical);
CREATE INDEX IF NOT EXISTS inventory_embedding_idx
    ON inventory_items USING hnsw (embedding vector_cosine_ops);

-- Production batch tracking (cure status, QC, ingredient usage)
CREATE TABLE IF NOT EXISTS production_batches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    batch_number TEXT UNIQUE NOT NULL,
    product_type TEXT NOT NULL,
    -- product_type: shampoo_bar | conditioner_bar
    batch_date DATE NOT NULL,
    quantity_produced INTEGER NOT NULL,
    cure_complete_date DATE,        -- NULL until cured
    status TEXT NOT NULL DEFAULT 'curing',
    -- status: curing | cured | in_stock | depleted
    ph_test_result DECIMAL(4,2),
    qc_notes TEXT,
    ingredients_used JSONB DEFAULT '[]',  -- [{sku, amount_g, unit}]
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS batches_status_idx ON production_batches (status);
CREATE INDEX IF NOT EXISTS batches_date_idx   ON production_batches (batch_date DESC);

-- ── Orders ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_number TEXT UNIQUE NOT NULL,
    customer_name TEXT,
    customer_email TEXT,
    channel TEXT NOT NULL DEFAULT 'shopify',
    -- channel: shopify | local_market | subscription
    status TEXT NOT NULL DEFAULT 'pending',
    -- status: pending | processing | shipped | delivered |
    --         refund_requested | refunded | cancelled
    items JSONB NOT NULL DEFAULT '[]',  -- [{sku, name, qty, unit_price}]
    subtotal DECIMAL(10,2),
    shipping DECIMAL(10,2) DEFAULT 0,
    tax DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2),
    shipping_address JSONB,
    tracking_number TEXT,
    is_subscription BOOLEAN DEFAULT FALSE,
    subscription_interval_days INTEGER,
    notes TEXT,
    guarantee_expires_at DATE,  -- order_date + 60 days
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS orders_number_idx  ON orders (order_number);
CREATE INDEX IF NOT EXISTS orders_status_idx  ON orders (status);
CREATE INDEX IF NOT EXISTS orders_email_idx   ON orders (customer_email);
CREATE INDEX IF NOT EXISTS orders_created_idx ON orders (created_at DESC);

-- ── FAQ / customer support ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS faq_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    category TEXT NOT NULL,
    -- category: usage | ingredients | guarantee | ordering | shipping |
    --           production | science
    embedding vector(768),
    usage_count INTEGER DEFAULT 0,
    guardrail TEXT,  -- e.g. 'no_medical_advice'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS faq_embedding_idx ON faq_entries
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS faq_category_idx ON faq_entries (category);

-- ── Meal planning (schema only — tools added later) ───────────────────────────

CREATE TABLE IF NOT EXISTS recipes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    ingredients JSONB DEFAULT '[]',
    instructions TEXT,
    servings INTEGER,
    prep_time_minutes INTEGER,
    tags TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS meal_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    week_start DATE NOT NULL UNIQUE,
    meals JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS grocery_lists (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    meal_plan_id UUID REFERENCES meal_plans(id) ON DELETE SET NULL,
    items JSONB DEFAULT '[]',
    completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Vector similarity search functions ───────────────────────────────────────

CREATE OR REPLACE FUNCTION search_thoughts(
    query_embedding vector(768),
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 10,
    filter_metadata jsonb DEFAULT NULL)
RETURNS TABLE (
    id UUID, content TEXT, metadata JSONB, source TEXT,
    created_at TIMESTAMPTZ, similarity float)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT t.id, t.content, t.metadata, t.source, t.created_at,
           1 - (t.embedding <=> query_embedding) AS similarity
    FROM thoughts t
    WHERE t.embedding IS NOT NULL
      AND 1 - (t.embedding <=> query_embedding) >= match_threshold
      AND (filter_metadata IS NULL OR t.metadata @> filter_metadata)
    ORDER BY t.embedding <=> query_embedding
    LIMIT match_count;
END; $$;

CREATE OR REPLACE FUNCTION search_household_knowledge(
    query_embedding vector(768),
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 5,
    filter_category text DEFAULT NULL)
RETURNS TABLE (
    id UUID, category TEXT, key TEXT, value TEXT,
    notes TEXT, similarity float)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT h.id, h.category, h.key, h.value, h.notes,
           1 - (h.embedding <=> query_embedding) AS similarity
    FROM household_knowledge h
    WHERE h.embedding IS NOT NULL
      AND 1 - (h.embedding <=> query_embedding) >= match_threshold
      AND (filter_category IS NULL OR h.category = filter_category)
    ORDER BY h.embedding <=> query_embedding
    LIMIT match_count;
END; $$;

CREATE OR REPLACE FUNCTION search_faq(
    query_embedding vector(768),
    match_threshold float DEFAULT 0.5,
    match_count int DEFAULT 5,
    filter_category text DEFAULT NULL)
RETURNS TABLE (
    id UUID, question TEXT, answer TEXT, category TEXT,
    guardrail TEXT, usage_count INTEGER, similarity float)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT f.id, f.question, f.answer, f.category,
           f.guardrail, f.usage_count,
           1 - (f.embedding <=> query_embedding) AS similarity
    FROM faq_entries f
    WHERE f.embedding IS NOT NULL
      AND 1 - (f.embedding <=> query_embedding) >= match_threshold
      AND (filter_category IS NULL OR f.category = filter_category)
    ORDER BY f.embedding <=> query_embedding
    LIMIT match_count;
END; $$;
