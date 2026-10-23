# テーブル定義書

> `scripts/gen_schema_dictionary.py` が ORM メタデータから生成。手で編集しない。
> トリガー（追記専用・配置版不変・ID 対応の参照整合）は `alembic/versions/0001_initial_schema.py` を参照。

## analysis_results

設定推測等の分析結果。確定設定としては扱わない（観測事実テーブルには書かない）。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `target_type` | TEXT | 不可 |  |  |
| `target_id` | UUID | 可 |  |  |
| `target_descriptor` | JSONB | 不可 | '{}' |  |
| `period_from` | DATE | 可 |  |  |
| `period_to` | DATE | 可 |  |  |
| `method` | TEXT | 不可 |  |  |
| `model_version` | TEXT | 不可 |  |  |
| `input_data_version` | JSONB | 不可 | '{}' |  |
| `result` | JSONB | 不可 |  |  |
| `computed_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `reliability` | NUMERIC(5, 4) | 可 |  |  |
| `sample_size` | INTEGER | 可 |  |  |

**制約・索引**

- CHECK `ck_analysis_results_period_range`: `period_to IS NULL OR period_from IS NULL OR period_to >= period_from`
- CHECK `ck_analysis_results_sample_size_non_negative`: `sample_size IS NULL OR sample_size >= 0`
- INDEX `ix_analysis_results_target`: (target_type, target_id)

## bonus_event_series

ある取得時点の当たり履歴一覧全体。系列ごとに全件保持し、古い系列は消さない。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `source_observation_id` | UUID | 不可 |  | source_observations.id |
| `hall_id` | UUID | 不可 |  | halls.id |
| `business_date` | DATE | 不可 |  |  |
| `machine_number` | TEXT | 不可 |  |  |
| `series_status` | TEXT | 不可 |  |  |
| `event_count` | INTEGER | 不可 |  |  |

**制約・索引**

- CHECK `ck_bonus_event_series_series_status_valid`: `series_status IN ('complete', 'partial', 'empty', 'unavailable')`
- INDEX `ix_bonus_event_series_key`: (hall_id, business_date, machine_number)
- UNIQUE INDEX `uq_bonus_event_series_observation`: (source_observation_id)

## bonus_events

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `series_id` | UUID | 不可 |  | bonus_event_series.id |
| `ordinal` | INTEGER | 不可 |  |  |
| `source_event_id` | TEXT | 可 |  |  |
| `event_key` | TEXT | 不可 |  |  |
| `event_time` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `hit_games` | BIGINT | 可 |  |  |
| `cumulative_games` | BIGINT | 可 |  |  |
| `raw_label` | TEXT | 不可 |  |  |
| `normalized_type` | TEXT | 可 |  |  |
| `payout_medals` | BIGINT | 可 |  |  |
| `raw_attributes` | JSONB | 不可 | '{}' |  |
| `quality_status` | TEXT | 不可 |  |  |

**制約・索引**

- CHECK `ck_bonus_events_cumulative_non_negative`: `cumulative_games IS NULL OR cumulative_games >= 0`
- CHECK `ck_bonus_events_hit_games_non_negative`: `hit_games IS NULL OR hit_games >= 0`
- CHECK `ck_bonus_events_ordinal_positive`: `ordinal >= 1`
- CHECK `ck_bonus_events_payout_non_negative`: `payout_medals IS NULL OR payout_medals >= 0`
- CHECK `ck_bonus_events_quality_status_valid`: `quality_status IN ('valid', 'warning', 'quarantined', 'unavailable')`
- INDEX `ix_bonus_events_event_key`: (event_key)
- UNIQUE INDEX `uq_bonus_events_series_ordinal`: (series_id, ordinal)

## collection_runs

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `source_id` | UUID | 不可 |  | sources.id |
| `hall_id` | UUID | 可 |  | halls.id |
| `started_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `finished_at` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `status` | TEXT | 不可 |  |  |
| `requested_range` | JSONB | 不可 | '{}' |  |
| `counts` | JSONB | 不可 | '{}' |  |
| `error_summary` | TEXT | 可 |  |  |
| `adapter_version` | TEXT | 不可 |  |  |
| `resumed_from_id` | UUID | 可 |  | collection_runs.id |

**制約・索引**

- CHECK `ck_collection_runs_finished_after_start`: `finished_at IS NULL OR finished_at >= started_at`
- CHECK `ck_collection_runs_status_valid`: `status IN ('running', 'succeeded', 'partial', 'failed')`
- INDEX `ix_collection_runs_source_started`: (source_id, started_at)

## daily_machine_stats

公開用の最新採用値。このテーブルのみ UPDATE で最新化し、変化は daily_stat_revisions に追記する。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `hall_id` | UUID | 不可 |  | halls.id |
| `business_date` | DATE | 不可 |  |  |
| `machine_number` | TEXT | 不可 |  |  |
| `installation_id` | UUID | 可 |  | machine_installations.id |
| `model_id` | UUID | 可 |  | machine_models.id |
| `total_games` | BIGINT | 可 |  |  |
| `normal_games` | BIGINT | 可 |  |  |
| `net_medals` | BIGINT | 可 |  |  |
| `bb_count` | INTEGER | 可 |  |  |
| `rb_count` | INTEGER | 可 |  |  |
| `at_count` | INTEGER | 可 |  |  |
| `cz_count` | INTEGER | 可 |  |  |
| `first_hit_count` | INTEGER | 可 |  |  |
| `last_games` | BIGINT | 可 |  |  |
| `max_net_medals` | BIGINT | 可 |  |  |
| `min_net_medals` | BIGINT | 可 |  |  |
| `max_payout_medals` | BIGINT | 可 |  |  |
| `field_status` | JSONB | 不可 | '{}' |  |
| `data_status` | TEXT | 不可 |  |  |
| `quality_status` | TEXT | 不可 |  |  |
| `is_corrected` | BOOLEAN | 不可 | false |  |
| `selected_observation_id` | UUID | 可 |  | source_observations.id |
| `selected_source_id` | UUID | 可 |  | sources.id |
| `revision_number` | INTEGER | 不可 |  |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- CHECK `ck_daily_machine_stats_at_count_non_negative`: `at_count IS NULL OR at_count >= 0`
- CHECK `ck_daily_machine_stats_bb_count_non_negative`: `bb_count IS NULL OR bb_count >= 0`
- CHECK `ck_daily_machine_stats_cz_count_non_negative`: `cz_count IS NULL OR cz_count >= 0`
- CHECK `ck_daily_machine_stats_data_status_valid`: `data_status IN ('provisional', 'final_candidate', 'final')`
- CHECK `ck_daily_machine_stats_first_hit_count_non_negative`: `first_hit_count IS NULL OR first_hit_count >= 0`
- CHECK `ck_daily_machine_stats_last_games_non_negative`: `last_games IS NULL OR last_games >= 0`
- CHECK `ck_daily_machine_stats_max_payout_medals_non_negative`: `max_payout_medals IS NULL OR max_payout_medals >= 0`
- CHECK `ck_daily_machine_stats_normal_games_non_negative`: `normal_games IS NULL OR normal_games >= 0`
- CHECK `ck_daily_machine_stats_normal_le_total`: `normal_games IS NULL OR total_games IS NULL OR normal_games <= total_games`
- CHECK `ck_daily_machine_stats_quality_status_valid`: `quality_status IN ('valid', 'warning', 'quarantined', 'unavailable')`
- CHECK `ck_daily_machine_stats_rb_count_non_negative`: `rb_count IS NULL OR rb_count >= 0`
- CHECK `ck_daily_machine_stats_revision_positive`: `revision_number >= 1`
- CHECK `ck_daily_machine_stats_total_games_non_negative`: `total_games IS NULL OR total_games >= 0`
- INDEX `ix_daily_machine_stats_hall_date`: (hall_id, business_date)
- INDEX `ix_daily_machine_stats_model_date`: (model_id, business_date)
- UNIQUE INDEX `uq_daily_machine_stats_key`: (hall_id, business_date, machine_number)

## daily_stat_revisions

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `daily_stat_id` | UUID | 不可 |  | daily_machine_stats.id |
| `revision_number` | INTEGER | 不可 |  |  |
| `recorded_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `previous_values` | JSONB | 可 |  |  |
| `new_values` | JSONB | 不可 |  |  |
| `reason` | TEXT | 不可 |  |  |
| `source_observation_id` | UUID | 可 |  | source_observations.id |
| `manual_correction_id` | UUID | 可 |  | manual_corrections.id |
| `selection_rule_version` | TEXT | 不可 |  |  |

**制約・索引**

- CHECK `ck_daily_stat_revisions_has_evidence`: `source_observation_id IS NOT NULL OR manual_correction_id IS NOT NULL`
- CHECK `ck_daily_stat_revisions_reason_valid`: `reason IN ('initial', 'source_update', 'status_change', 'correction', 'manual_correction', 'source_switch')`
- UNIQUE INDEX `uq_daily_stat_revisions_stat_rev`: (daily_stat_id, revision_number)

## data_quality_issues

品質検証の検出結果。理由コード・重大度・対象・検出日時を記録する。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `detected_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `issue_code` | TEXT | 不可 |  |  |
| `severity` | TEXT | 不可 |  |  |
| `entity_type` | TEXT | 不可 |  |  |
| `entity_id` | UUID | 可 |  |  |
| `hall_id` | UUID | 可 |  | halls.id |
| `business_date` | DATE | 可 |  |  |
| `machine_number` | TEXT | 可 |  |  |
| `dedup_key` | TEXT | 不可 |  |  |
| `details` | JSONB | 不可 | '{}' |  |
| `status` | TEXT | 不可 | 'open' |  |
| `resolved_at` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `resolution_note` | TEXT | 可 |  |  |

**制約・索引**

- CHECK `ck_data_quality_issues_severity_valid`: `severity IN ('info', 'warning', 'error', 'critical')`
- CHECK `ck_data_quality_issues_status_valid`: `status IN ('open', 'acknowledged', 'resolved')`
- INDEX `ix_data_quality_issues_entity`: (entity_type, entity_id)
- INDEX `ix_data_quality_issues_key`: (hall_id, business_date, machine_number)
- INDEX `ix_data_quality_issues_open`: (status, severity)
- UNIQUE INDEX `uq_data_quality_issues_dedup`: (issue_code, dedup_key)

## hall_events

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `hall_id` | UUID | 不可 |  | halls.id |
| `business_date` | DATE | 不可 |  |  |
| `event_name` | TEXT | 不可 |  |  |
| `event_category` | TEXT | 可 |  |  |
| `evidence` | TEXT | 可 |  |  |
| `source_id` | UUID | 可 |  | sources.id |
| `source_description` | TEXT | 可 |  |  |
| `reliability` | NUMERIC(5, 4) | 可 |  |  |
| `confirmation_status` | TEXT | 不可 | 'unconfirmed' |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- CHECK `ck_hall_events_confirmation_status_valid`: `confirmation_status IN ('unconfirmed', 'confirmed', 'rejected')`
- CHECK `ck_hall_events_reliability_range`: `reliability IS NULL OR (reliability >= 0 AND reliability <= 1)`
- INDEX `ix_hall_events_hall_date`: (hall_id, business_date)

## hall_islands

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `layout_version_id` | UUID | 不可 |  | hall_layout_versions.id |
| `island_code` | TEXT | 不可 |  |  |
| `display_name` | TEXT | 不可 |  |  |
| `geometry` | JSONB | 可 |  |  |
| `notes` | TEXT | 可 |  |  |

**制約・索引**

- UNIQUE `uq_hall_islands_id_version`: (id, layout_version_id)
- UNIQUE `uq_hall_islands_layout_version_id_island_code`: (layout_version_id, island_code)

## hall_layout_versions

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `hall_id` | UUID | 不可 |  | halls.id |
| `version_number` | INTEGER | 不可 |  |  |
| `valid_from` | DATE | 不可 |  |  |
| `valid_to` | DATE | 可 |  |  |
| `status` | TEXT | 不可 |  |  |
| `created_by` | TEXT | 不可 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |
| `published_at` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `source_document_uri` | TEXT | 可 |  |  |
| `layout_metadata` | JSONB | 不可 | '{}' |  |
| `notes` | TEXT | 可 |  |  |

**制約・索引**

- CHECK `ck_hall_layout_versions_status_valid`: `status IN ('draft', 'published', 'rejected')`
- CHECK `ck_hall_layout_versions_valid_range`: `valid_to IS NULL OR valid_to > valid_from`
- CHECK `ck_hall_layout_versions_version_positive`: `version_number >= 1`
- `ex_hall_layout_versions_published_no_overlap`: `EXCLUDE USING gist (hall_id WITH =, daterange(valid_from, valid_to, '[)') WITH &&) WHERE (status = 'published')`
- UNIQUE `uq_hall_layout_versions_hall_id_version_number`: (hall_id, version_number)

## halls

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `code` | TEXT | 不可 |  |  |
| `name` | TEXT | 不可 |  |  |
| `address` | TEXT | 可 |  |  |
| `timezone` | TEXT | 不可 | 'Asia/Tokyo' |  |
| `business_day_cutoff` | TIME WITHOUT TIME ZONE | 可 |  |  |
| `closing_time` | TIME WITHOUT TIME ZONE | 可 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |
| `updated_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- CHECK `ck_halls_code_not_empty`: `code <> ''`
- UNIQUE `uq_halls_code`: (code)

## layout_imports

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `hall_id` | UUID | 不可 |  | halls.id |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |
| `file_uri` | TEXT | 不可 |  |  |
| `file_sha256` | VARCHAR(64) | 不可 |  |  |
| `validation_report` | JSONB | 不可 |  |  |
| `status` | TEXT | 不可 |  |  |
| `target_layout_version_id` | UUID | 可 |  | hall_layout_versions.id |
| `created_by` | TEXT | 不可 |  |  |

**制約・索引**

- CHECK `ck_layout_imports_sha256_hex`: `file_sha256 ~ '^[0-9a-f]{64}$'`
- CHECK `ck_layout_imports_status_valid`: `status IN ('invalid', 'previewed', 'published', 'rejected')`

## machine_adjacencies

位置関係（無向）。position_a_id < position_b_id に正規化して重複を禁止する。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `layout_version_id` | UUID | 不可 |  | machine_positions.layout_version_id, hall_layout_versions.id, machine_positions.layout_version_id |
| `position_a_id` | UUID | 不可 |  | machine_positions.id |
| `position_b_id` | UUID | 不可 |  | machine_positions.id |
| `relation_type` | TEXT | 不可 |  |  |
| `is_manual_override` | BOOLEAN | 不可 | false |  |
| `notes` | TEXT | 可 |  |  |

**制約・索引**

- CHECK `ck_machine_adjacencies_ordered_pair`: `position_a_id < position_b_id`
- CHECK `ck_machine_adjacencies_relation_type_valid`: `relation_type IN ('next_to', 'across_aisle', 'back_to_back')`
- 複合 FK `fk_machine_adjacencies_a_same_version`: (position_a_id, layout_version_id) → machine_positions(id, layout_version_id)
- 複合 FK `fk_machine_adjacencies_b_same_version`: (position_b_id, layout_version_id) → machine_positions(id, layout_version_id)
- UNIQUE `uq_machine_adjacencies_layout_version_id_position_a_id_position_b_id_relation_type`: (layout_version_id, position_a_id, position_b_id, relation_type)

## machine_installations

店舗＋台番号ごとの機種設置期間。期間は [valid_from, valid_to) の半開区間。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `hall_id` | UUID | 不可 |  | halls.id |
| `machine_number` | TEXT | 不可 |  |  |
| `model_id` | UUID | 可 |  | machine_models.id |
| `valid_from` | DATE | 不可 |  |  |
| `valid_to` | DATE | 可 |  |  |
| `source_id` | UUID | 可 |  | sources.id |
| `evidence_raw_id` | UUID | 可 |  | raw_payloads.id |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- CHECK `ck_machine_installations_machine_number_not_empty`: `machine_number <> ''`
- CHECK `ck_machine_installations_valid_range`: `valid_to IS NULL OR valid_to > valid_from`
- `ex_machine_installations_no_overlap`: `EXCLUDE USING gist (hall_id WITH =, machine_number WITH =, daterange(valid_from, valid_to, '[)') WITH &&)`
- INDEX `ix_machine_installations_model`: (model_id)

## machine_models

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `canonical_name` | TEXT | 不可 |  |  |
| `manufacturer` | TEXT | 可 |  |  |
| `model_type` | TEXT | 可 |  |  |
| `release_date` | DATE | 可 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- INDEX `ix_machine_models_canonical_name`: (canonical_name)

## machine_positions

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `layout_version_id` | UUID | 不可 |  | hall_islands.layout_version_id, hall_layout_versions.id |
| `island_id` | UUID | 不可 |  | hall_islands.id |
| `machine_number` | TEXT | 可 |  |  |
| `side` | TEXT | 不可 |  |  |
| `sequence_no` | INTEGER | 不可 |  |  |
| `x` | NUMERIC | 可 |  |  |
| `y` | NUMERIC | 可 |  |  |
| `rotation_deg` | NUMERIC | 可 |  |  |
| `position_kind` | TEXT | 不可 | 'machine' |  |
| `notes` | TEXT | 可 |  |  |

**制約・索引**

- CHECK `ck_machine_positions_machine_number_iff_machine`: `(position_kind = 'machine') = (machine_number IS NOT NULL)`
- CHECK `ck_machine_positions_position_kind_valid`: `position_kind IN ('machine', 'empty', 'aisle', 'pillar')`
- CHECK `ck_machine_positions_sequence_positive`: `sequence_no >= 1`
- CHECK `ck_machine_positions_side_not_empty`: `side <> ''`
- 複合 FK `fk_machine_positions_island_same_version`: (island_id, layout_version_id) → hall_islands(id, layout_version_id)
- UNIQUE `uq_machine_positions_id_version`: (id, layout_version_id)
- UNIQUE `uq_machine_positions_slot`: (layout_version_id, island_id, side, sequence_no)
- UNIQUE INDEX `uq_machine_positions_version_machine`: (layout_version_id, machine_number) WHERE machine_number IS NOT NULL

## machine_snapshots

営業中の各時点の指標。元観測と 1 対 1。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `source_observation_id` | UUID | 不可 |  | source_observations.id |
| `hall_id` | UUID | 不可 |  | halls.id |
| `business_date` | DATE | 不可 |  |  |
| `machine_number` | TEXT | 不可 |  |  |
| `captured_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `metrics` | JSONB | 不可 |  |  |
| `completeness_status` | TEXT | 不可 |  |  |

**制約・索引**

- CHECK `ck_machine_snapshots_completeness_status_valid`: `completeness_status IN ('complete', 'partial', 'empty')`
- INDEX `ix_machine_snapshots_key_captured`: (hall_id, business_date, machine_number, captured_at DESC)
- UNIQUE INDEX `uq_machine_snapshots_observation`: (source_observation_id)

## machine_specs

公表値（published）と推定値（estimated）を value_kind で分離する。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `model_id` | UUID | 不可 |  | machine_models.id |
| `setting_value` | TEXT | 不可 |  |  |
| `metric_name` | TEXT | 不可 |  |  |
| `numerator` | NUMERIC | 可 |  |  |
| `denominator` | NUMERIC | 可 |  |  |
| `value` | NUMERIC | 可 |  |  |
| `unit` | TEXT | 可 |  |  |
| `condition` | TEXT | 可 |  |  |
| `value_kind` | TEXT | 不可 |  |  |
| `citation` | TEXT | 不可 |  |  |
| `valid_from` | DATE | 可 |  |  |
| `valid_to` | DATE | 可 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- CHECK `ck_machine_specs_denominator_positive`: `denominator IS NULL OR denominator > 0`
- CHECK `ck_machine_specs_valid_range`: `valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from`
- CHECK `ck_machine_specs_value_kind_valid`: `value_kind IN ('published', 'estimated')`
- INDEX `ix_machine_specs_model_setting`: (model_id, setting_value, metric_name)

## manual_corrections

人手による訂正。元観測は改変せず、採用値への上書きとして監査可能に保持する。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `hall_id` | UUID | 不可 |  | halls.id |
| `business_date` | DATE | 不可 |  |  |
| `machine_number` | TEXT | 不可 |  |  |
| `field_name` | TEXT | 不可 |  |  |
| `new_value` | NUMERIC(20, 0) | 可 |  |  |
| `field_status` | TEXT | 不可 | 'observed' |  |
| `reason` | TEXT | 不可 |  |  |
| `corrected_by` | TEXT | 不可 |  |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |
| `revoked_at` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `revoked_by` | TEXT | 可 |  |  |

**制約・索引**

- CHECK `ck_manual_corrections_revoked_after_created`: `revoked_at IS NULL OR revoked_at >= created_at`
- INDEX `ix_manual_corrections_key`: (hall_id, business_date, machine_number)

## parse_runs

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `raw_payload_id` | UUID | 不可 |  | raw_payloads.id |
| `parser_name` | TEXT | 不可 |  |  |
| `parser_version` | TEXT | 不可 |  |  |
| `started_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `finished_at` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `status` | TEXT | 不可 |  |  |
| `issues` | JSONB | 不可 | '[]' |  |

**制約・索引**

- CHECK `ck_parse_runs_status_valid`: `status IN ('running', 'succeeded', 'failed')`
- UNIQUE INDEX `uq_parse_runs_raw_parser`: (raw_payload_id, parser_name, parser_version)

## raw_payloads

取得イベント 1 件 = 1 行。ファイル本体は sha256 で重複排除され、同一内容は同じ storage_uri を指す。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `collection_run_id` | UUID | 不可 |  | collection_runs.id |
| `source_id` | UUID | 不可 |  | sources.id |
| `hall_id` | UUID | 可 |  | halls.id |
| `fetched_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `source_reported_at` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `request_fingerprint` | TEXT | 不可 |  |  |
| `resource_locator` | TEXT | 可 |  |  |
| `content_type` | TEXT | 不可 |  |  |
| `storage_uri` | TEXT | 不可 |  |  |
| `sha256` | VARCHAR(64) | 不可 |  |  |
| `byte_size` | BIGINT | 不可 |  |  |
| `response_metadata` | JSONB | 不可 | '{}' |  |
| `capture_status` | TEXT | 不可 |  |  |
| `duplicate_of_id` | UUID | 可 |  | raw_payloads.id |

**制約・索引**

- CHECK `ck_raw_payloads_byte_size_non_negative`: `byte_size >= 0`
- CHECK `ck_raw_payloads_capture_status_valid`: `capture_status IN ('stored', 'duplicate_content', 'manual_import')`
- CHECK `ck_raw_payloads_duplicate_has_origin`: `(capture_status = 'duplicate_content') = (duplicate_of_id IS NOT NULL)`
- CHECK `ck_raw_payloads_sha256_hex`: `sha256 ~ '^[0-9a-f]{64}$'`
- INDEX `ix_raw_payloads_hall_fetched`: (hall_id, fetched_at)
- INDEX `ix_raw_payloads_run_fingerprint`: (collection_run_id, request_fingerprint)
- INDEX `ix_raw_payloads_sha256`: (sha256)

## slump_points

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `series_id` | UUID | 不可 |  | slump_series.id |
| `point_index` | INTEGER | 不可 |  |  |
| `x_raw` | TEXT | 可 |  |  |
| `y_raw` | TEXT | 可 |  |  |
| `x_numeric` | NUMERIC | 可 |  |  |
| `x_timestamp` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `y_numeric` | NUMERIC | 可 |  |  |
| `estimated_net_medals` | NUMERIC | 可 |  |  |
| `point_metadata` | JSONB | 不可 | '{}' |  |
| `quality_status` | TEXT | 不可 |  |  |

**制約・索引**

- CHECK `ck_slump_points_point_index_non_negative`: `point_index >= 0`
- CHECK `ck_slump_points_quality_status_valid`: `quality_status IN ('valid', 'warning', 'quarantined', 'unavailable')`
- UNIQUE INDEX `uq_slump_points_series_index`: (series_id, point_index)

## slump_series

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `source_observation_id` | UUID | 不可 |  | source_observations.id |
| `series_key` | TEXT | 不可 | 'main' |  |
| `hall_id` | UUID | 不可 |  | halls.id |
| `business_date` | DATE | 不可 |  |  |
| `machine_number` | TEXT | 不可 |  |  |
| `captured_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `x_axis_type` | TEXT | 不可 |  |  |
| `x_unit` | TEXT | 可 |  |  |
| `y_axis_type` | TEXT | 不可 |  |  |
| `y_unit` | TEXT | 可 |  |  |
| `origin_type` | TEXT | 不可 |  |  |
| `derivation_method` | TEXT | 可 |  |  |
| `derivation_version` | TEXT | 可 |  |  |
| `derived_from_series_id` | UUID | 可 |  | slump_series.id |
| `raw_payload_id` | UUID | 不可 |  | raw_payloads.id |
| `point_count` | INTEGER | 不可 |  |  |
| `series_metadata` | JSONB | 不可 | '{}' |  |
| `quality_status` | TEXT | 不可 |  |  |

**制約・索引**

- CHECK `ck_slump_series_estimated_has_derivation`: `origin_type = 'observed' OR (derivation_method IS NOT NULL AND derivation_version IS NOT NULL)`
- CHECK `ck_slump_series_origin_type_valid`: `origin_type IN ('observed', 'estimated')`
- CHECK `ck_slump_series_quality_status_valid`: `quality_status IN ('valid', 'warning', 'quarantined', 'unavailable')`
- CHECK `ck_slump_series_x_axis_type_valid`: `x_axis_type IN ('time', 'cumulative_games', 'pixel', 'unknown')`
- CHECK `ck_slump_series_y_axis_type_valid`: `y_axis_type IN ('net_medals', 'pixel', 'unknown')`
- INDEX `ix_slump_series_key_captured`: (hall_id, business_date, machine_number, captured_at DESC)
- UNIQUE INDEX `uq_slump_series_observation_key`: (source_observation_id, series_key)

## source_mappings

取得元の店舗/機種 ID・名称と内部エンティティの対応。  internal_entity_id の参照整合性はトリガー ``trg_source_mappings_validate`` で entity_type 別に検証する。 未レビュー（pending）の間は internal_entity_id を NULL にできる。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `source_id` | UUID | 不可 |  | sources.id |
| `entity_type` | TEXT | 不可 |  |  |
| `external_id` | TEXT | 不可 |  |  |
| `internal_entity_id` | UUID | 可 |  |  |
| `external_name` | TEXT | 可 |  |  |
| `scope_hall_id` | UUID | 可 |  | halls.id |
| `valid_from` | DATE | 可 |  |  |
| `valid_to` | DATE | 可 |  |  |
| `confidence` | NUMERIC(5, 4) | 可 |  |  |
| `review_status` | TEXT | 不可 | 'pending' |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- CHECK `ck_source_mappings_confidence_range`: `confidence IS NULL OR (confidence >= 0 AND confidence <= 1)`
- CHECK `ck_source_mappings_entity_type_valid`: `entity_type IN ('hall', 'machine_model')`
- CHECK `ck_source_mappings_matched_has_entity`: `review_status NOT IN ('confirmed', 'auto_matched') OR internal_entity_id IS NOT NULL`
- CHECK `ck_source_mappings_review_status_valid`: `review_status IN ('pending', 'auto_matched', 'confirmed', 'rejected')`
- CHECK `ck_source_mappings_valid_range`: `valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from`
- INDEX `ix_source_mappings_internal`: (entity_type, internal_entity_id)
- UNIQUE INDEX `uq_source_mappings_key`: (source_id, entity_type, external_id, coalesce(scope_hall_id, '00000000-0000-0000-0000-000000000000'::uuid), coalesce(valid_from, '-infinity'::date))

## source_observations

取得元別・時点別の観測値。field_values は正規化名→値、field_metadata は正規化名→{status, raw, label, ...}。

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `parse_run_id` | UUID | 不可 |  | parse_runs.id |
| `source_id` | UUID | 不可 |  | sources.id |
| `hall_id` | UUID | 不可 |  | halls.id |
| `machine_number` | TEXT | 不可 |  |  |
| `source_machine_number` | TEXT | 不可 |  |  |
| `business_date` | DATE | 不可 |  |  |
| `observed_at` | TIMESTAMP WITH TIME ZONE | 不可 |  |  |
| `source_reported_at` | TIMESTAMP WITH TIME ZONE | 可 |  |  |
| `source_model_name` | TEXT | 可 |  |  |
| `source_model_id` | TEXT | 可 |  |  |
| `source_record_id` | TEXT | 可 |  |  |
| `field_values` | JSONB | 不可 | '{}' |  |
| `field_metadata` | JSONB | 不可 | '{}' |  |
| `quality_status` | TEXT | 不可 |  |  |

**制約・索引**

- CHECK `ck_source_observations_quality_status_valid`: `quality_status IN ('valid', 'warning', 'quarantined', 'unavailable')`
- INDEX `ix_source_observations_key_observed`: (hall_id, business_date, machine_number, observed_at DESC)
- UNIQUE INDEX `uq_source_observations_parse_key`: (parse_run_id, hall_id, business_date, machine_number)

## sources

| 列 | 型 | NULL | 既定値 | 参照 |
|---|---|---|---|---|
| `id` (PK) | UUID | 不可 | アプリ側生成（UUIDv7） |  |
| `code` | TEXT | 不可 |  |  |
| `name` | TEXT | 可 |  |  |
| `enabled` | BOOLEAN | 不可 | true |  |
| `created_at` | TIMESTAMP WITH TIME ZONE | 不可 | now() |  |

**制約・索引**

- UNIQUE `uq_sources_code`: (code)

