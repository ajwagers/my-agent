-- Summit Pine seed data
-- NOTE: embeddings are populated by the open-brain-mcp startup routine

-- ── Raw materials ─────────────────────────────────────────────────────────────

INSERT INTO inventory_items (sku, name, category, unit, quantity_on_hand, reorder_threshold, reorder_quantity, unit_cost, supplier, supplier_lead_days, is_critical, notes)
VALUES
  ('RAW-COCO',   'Organic Coconut Oil 76°',        'raw_material', 'g',   0, 2000, 5000, 0.0080, 'Bulk Apothecary', 4, FALSE,
   'Primary lather oil, 60% of shampoo bar. Also available locally: Walmart, ALDI, Publix.'),
  ('RAW-OLIVE',  'Organic Olive Oil',              'raw_material', 'g',   0, 1000, 3000, 0.0060, 'Bulk Apothecary', 4, FALSE,
   '30% of shampoo bar, conditioning. Local: Publix, ALDI, Walmart cooking section.'),
  ('RAW-CASTOR', 'Organic Castor Oil',             'raw_material', 'g',   0,  500, 1500, 0.0120, 'Bulk Apothecary', 4, FALSE,
   '10% of shampoo bar, lather booster. Local: Walmart health aisle, Publix pharmacy.'),
  ('RAW-LYE',    'NaOH Lye 99% Pure',             'raw_material', 'g',   0, 1000, 5000, 0.0030, 'Essential Depot',  4, TRUE,
   'CRITICAL: hazmat shipping may delay. 108g/batch shampoo. Local emergency: Walmart soap-making aisle.'),
  ('RAW-PTAR',   'Creosote-Free Pine Tar',         'raw_material', 'g',   0,  300, 1500, 0.0600, 'Etsy (Sweet Harvest Farms)', 9, TRUE,
   'CRITICAL: 7-10 day lead time, longest of all materials. 68g/batch (10%). Local emergency: Tractor Supply equine section, Horse Health brand.'),
  ('RAW-TTEO',   'Tea Tree Essential Oil',         'raw_material', 'g',   0,  100,  500, 0.2000, 'Mountain Rose Herbs', 6, FALSE,
   '3.4% of shampoo bar, antifungal efficacy. Local: Publix NOW brand, Walmart, Hobby Lobby.'),
  ('RAW-CHAR',   'Activated Charcoal Powder',      'raw_material', 'g',   0,   50,  250, 0.0800, 'Bulk Apothecary', 4, FALSE,
   '0.7% of shampoo bar, detox. Local: Walmart health, Publix.'),
  ('RAW-NETTLE', 'Nettle Leaf Powder',             'raw_material', 'g',   0,   50,  250, 0.0500, 'Mountain Rose Herbs', 6, FALSE,
   'Optional 0.6% additive, scalp soothing. Local: Walmart supplements aisle.'),
  ('RAW-SHEA',   'Organic Unrefined Shea Butter',  'raw_material', 'g',   0,  500, 2000, 0.0200, 'Bulk Apothecary', 4, FALSE,
   '60% of conditioner bar. Local: Walmart (Palmer''s or raw), Publix.'),
  ('RAW-COCOA',  'Organic Cocoa Butter Wafers',    'raw_material', 'g',   0,  300, 1500, 0.0250, 'Bulk Apothecary', 4, FALSE,
   '40% of conditioner bar. Local: Walmart raw wafers, Hobby Lobby.'),
  ('RAW-ROSEO',  'Rosemary EO ct. cineole',        'raw_material', 'g',   0,   30,  150, 0.3000, 'Mountain Rose Herbs', 6, FALSE,
   'Conditioner bar scent option 1 (2.3g/batch). Local: Publix NOW brand.'),
  ('RAW-CEDAR',  'Cedarwood Atlas EO',             'raw_material', 'g',   0,   20,  100, 0.2500, 'Mountain Rose Herbs', 6, FALSE,
   'Conditioner bar scent option 2 alt (1.1g/batch). Local: Hobby Lobby, Walmart.'),
  ('RAW-WATER',  'Distilled Water',                'raw_material', 'ml',  0, 1000, 5000, 0.0005, 'local',            1, FALSE,
   '216ml/shampoo batch. All local grocery stores, pharmacy.'),
-- Packaging
  ('PKG-KRAFT',  'Kraft Paper Wrap',               'packaging', 'sheets', 0,   50,  200, 0.1500, 'Amazon',           3, FALSE, NULL),
  ('PKG-TWINE',  'Natural Twine',                  'packaging', 'm',      0,   10,   50, 0.0500, 'Amazon',           3, FALSE, NULL),
  ('PKG-LABEL',  'Custom Labels (Sticker Mule)',   'packaging', 'sheets', 0,   10,   50, 0.8000, 'Sticker Mule',     8, FALSE, 'Allow 7-10 days for custom print runs.')
ON CONFLICT (sku) DO NOTHING;

-- ── Finished goods ────────────────────────────────────────────────────────────

INSERT INTO inventory_items (sku, name, category, unit, quantity_on_hand, reorder_threshold, unit_cost, notes)
VALUES
  ('SP-SHAMPOO',    'Scalp Command Shampoo Bar',        'finished_good', 'bar', 0, 10, 0.80,
   '3oz bar, 60-80 washes. Cold-process, 4-6 week cure required. 12-16 bars per batch.'),
  ('SP-CONDITIONER','Scalp Recovery Conditioner Bar',   'finished_good', 'bar', 0, 10, 0.55,
   '2oz bar, 40-60 applications. Melt-pour, 24hr cure. 4-6 bars per batch.'),
  ('SP-DUO',        'Scalp Command Duo (Shampoo+Conditioner)', 'finished_good', 'duo', 0, 20, 1.35,
   'Core product. $28-30 retail, $4.35 material cost, 84-88% margin. 60-day Scalp Supremacy Guarantee.')
ON CONFLICT (sku) DO NOTHING;

-- ── FAQ entries (pre-seeded, embeddings added at startup) ────────────────────

INSERT INTO faq_entries (question, answer, category, guardrail) VALUES
  ('How do I use the Summit Pine Shampoo Bar?',
   'Wet your hair and the bar thoroughly. Rub the bar directly on your scalp or lather in your hands, then work through hair. Use 3 times per week minimum for best results. Follow with the Scalp Recovery Conditioner Bar. Allow 4-6 weeks to see full results.',
   'usage', NULL),

  ('How long will one shampoo bar last?',
   'The 3oz Scalp Command Shampoo Bar yields 60-80 washes. For 3x/week use that''s roughly 5-6 months per bar.',
   'usage', NULL),

  ('What is the 60-Day Scalp Supremacy Guarantee?',
   'First-purchase customers who see no improvement in flakes or scalp comfort after 45-60 days of use (minimum 3x/week) receive a full refund or store credit toward another product. Requires a brief survey (scalp type, usage frequency, results). Refunds are issued as store credit after the first claim. Applies to first duo purchase only.',
   'guarantee', NULL),

  ('What ingredients are in the shampoo bar?',
   'Organic coconut oil (60%), organic olive oil (30%), organic castor oil (10%), NaOH lye (saponified), creosote-free pine tar (10%), tea tree essential oil (3.4%), activated charcoal powder (0.7%), and optional nettle leaf powder (0.6%). All lye is consumed during saponification — the finished bar contains no active lye.',
   'ingredients', NULL),

  ('Is pine tar safe for my scalp?',
   'Summit Pine uses creosote-free pine tar, which has been used for scalp conditions for over a century. It is formulated specifically to suppress Malassezia yeast, the primary cause of dandruff. If you have a known skin condition, persistent irritation, or open wounds, please consult a dermatologist before use.',
   'ingredients', 'no_medical_advice'),

  ('Does the shampoo bar contain lye? Is it safe?',
   'Lye (sodium hydroxide) is used in the cold-process soap-making process, but it is completely consumed during saponification. The finished bar contains no active lye. It is safe for normal scalp use. Cure time is 4-6 weeks to ensure mild pH (target 8-10).',
   'science', NULL),

  ('Why does it take 4-6 weeks to see results?',
   'Pine tar works by suppressing Malassezia yeast overgrowth, which takes consistent application over several weeks to reduce. The scalp''s cell turnover cycle is approximately 28 days, so lasting improvement requires patience. Many customers see improvement within 3-4 weeks with 3x/week use.',
   'science', NULL),

  ('Can Summit Pine help with seborrheic dermatitis?',
   'The formula is specifically optimized for Malassezia yeast suppression using coconut lauric acid and tea tree oil, which are the primary drivers of seborrheic dandruff. However, we cannot make medical claims. If you have a diagnosed skin condition, please consult your dermatologist.',
   'science', 'no_medical_advice'),

  ('How do I use the Conditioner Bar?',
   'After shampooing, rub the Scalp Recovery Conditioner Bar between your palms to melt a small amount, then work through mid-lengths and ends. Avoid the scalp to prevent buildup. Rinse thoroughly. The anhydrous formula rinses clean without residue.',
   'usage', NULL),

  ('What scent options does the conditioner bar come in?',
   'Option 1 (Rosemary + Australian Sandalwood): fresh and woodsy. Option 2 (Rosemary + Cedarwood): rugged forest with economical wood depth. Option 3 (Frankincense): rich and meditative. Option 4 (Lavender): classic and calming. Current production uses Option 2 (Rosemary + Cedarwood) as the default.',
   'ingredients', NULL),

  ('What is your shipping policy?',
   'We offer free shipping on first duo orders. Subsequent orders ship at flat rate. Orders typically process within 2-3 business days. Subscription orders ship automatically every 60-90 days.',
   'shipping', NULL),

  ('Do you offer a subscription?',
   'Yes. Subscribe and save 10% — duos ship every 60 or 90 days based on your preference. You can pause or cancel any time.',
   'ordering', NULL),

  ('What is the price of the Scalp Command Duo?',
   'The Scalp Command Duo (Shampoo Bar + Conditioner Bar) retails for $28-30. The Full Field Kit (duo + beard oil + body bar) will be available at $48-52.',
   'ordering', NULL),

  ('How are Summit Pine products made?',
   'All products are handcrafted in small batches in Orlando, Florida using a cold-process soap method for shampoo bars and melt-pour for conditioner bars. Shampoo bars cure for 4-6 weeks to reach mild pH. We use organic oils and butters, creosote-free pine tar, and pure essential oils.',
   'production', NULL),

  ('Where do you source your ingredients?',
   'We source from long-term partners: oils and butters from Bulk Apothecary, essential oils from Mountain Rose Herbs, lye from Essential Depot, and pine tar from Sweet Harvest Farms (Etsy). We prioritize organic, traceable ingredients with consistent quality.',
   'ingredients', NULL)
ON CONFLICT DO NOTHING;
