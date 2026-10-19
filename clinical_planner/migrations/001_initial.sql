CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    title VARCHAR(200) NOT NULL CHECK(length(trim(title)) > 0),
    description TEXT NOT NULL DEFAULT '',
    category VARCHAR(30) NOT NULL CHECK(category IN ('Clinical','Governance','Administrative','Academic','Research','Correspondence','Personal')),
    source VARCHAR(80) NOT NULL,
    created_date DATE NOT NULL,
    due_date DATE,
    estimated_minutes INTEGER NOT NULL DEFAULT 30 CHECK(estimated_minutes > 0),
    status VARCHAR(20) NOT NULL CHECK(status IN ('Inbox','Ready','Scheduled','In Progress','Waiting','Completed','Deferred')),
    dependencies JSON NOT NULL DEFAULT '[]',
    last_verified_date DATE,
    safety_critical BOOLEAN NOT NULL DEFAULT 0,
    consequences INTEGER NOT NULL DEFAULT 10 CHECK(consequences BETWEEN 0 AND 30),
    importance INTEGER NOT NULL DEFAULT 10 CHECK(importance BETWEEN 0 AND 20),
    dependency_impact INTEGER NOT NULL DEFAULT 0 CHECK(dependency_impact BETWEEN 0 AND 10),
    completed_at DATETIME,
    deferred_to DATE
);
CREATE TABLE recurring_commitments (
    id INTEGER PRIMARY KEY,
    title VARCHAR(200) NOT NULL CHECK(length(trim(title)) > 0),
    weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
    start_time TIME NOT NULL,
    end_time TIME NOT NULL CHECK(end_time > start_time),
    source VARCHAR(80) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Confirmed' CHECK(status IN ('Confirmed','Tentative')),
    kind VARCHAR(30) NOT NULL DEFAULT 'commitment',
    location VARCHAR(200) NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT 1,
    effective_from DATE,
    effective_until DATE,
    CHECK(effective_until IS NULL OR effective_from IS NULL OR effective_until >= effective_from)
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    title VARCHAR(200) NOT NULL CHECK(length(trim(title)) > 0),
    start DATETIME NOT NULL,
    end DATETIME NOT NULL CHECK(end > start),
    source VARCHAR(80) NOT NULL,
    external_id VARCHAR(200),
    status VARCHAR(20) NOT NULL DEFAULT 'Confirmed' CHECK(status IN ('Confirmed','Tentative')),
    kind VARCHAR(30) NOT NULL DEFAULT 'meeting',
    location VARCHAR(200) NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    task_id INTEGER REFERENCES tasks(id),
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT uq_event_source_external UNIQUE(source, external_id)
);
CREATE TABLE settings (
    key VARCHAR(80) PRIMARY KEY,
    value JSON NOT NULL
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME NOT NULL,
    actor VARCHAR(100) NOT NULL DEFAULT 'local-demo',
    action VARCHAR(100) NOT NULL,
    entity VARCHAR(100) NOT NULL DEFAULT '',
    detail VARCHAR(500) NOT NULL DEFAULT ''
);
