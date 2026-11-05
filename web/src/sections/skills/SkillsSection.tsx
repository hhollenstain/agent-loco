import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../api";
import { notify, workspacePath } from "../../page";
import { repoLabel } from "../../repoLabel";
import type { Skill, SkillsPayload } from "./types";

const INFO_ICON = (
  <svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path
      d="M9.6 9.4a2.5 2.5 0 1 1 3.5 2.28c-.72.36-1.1.86-1.1 1.72V14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
    />
    <circle cx="12" cy="17" r="1.05" fill="currentColor" />
  </svg>
);

export function SkillsSection() {
  const [payload, setPayload] = useState<SkillsPayload | null>(null);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [openRepos, setOpenRepos] = useState<Record<string, boolean>>({});
  const [infoName, setInfoName] = useState("");
  const [info, setInfo] = useState<Skill | null>(null);
  const [infoError, setInfoError] = useState("");
  const [url, setUrl] = useState("");
  const [teamId, setTeamId] = useState("");

  useEffect(() => {
    function onSkills(event: Event) {
      setPayload((event as CustomEvent<SkillsPayload>).detail || {});
    }
    const cached = (window as SkillsWindow).__lbwProjectSkills;
    if (cached) setPayload(cached);
    document.addEventListener("lbw-project-skills", onSkills);
    return () => document.removeEventListener("lbw-project-skills", onSkills);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setApplied(query), 200);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && infoName) setInfoName("");
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [infoName]);

  useEffect(() => {
    if (!infoName) return;
    let cancelled = false;
    setInfo(null);
    setInfoError("");
    api<Skill>(
      `/api/workspaces/skills/detail?path=${encodeURIComponent(workspacePath())}&name=${encodeURIComponent(infoName)}`,
    )
      .then((body) => {
        if (!cancelled) setInfo(body);
      })
      .catch((err: unknown) => {
        if (!cancelled) setInfoError(err instanceof Error ? err.message : "failed to load skill");
      });
    return () => {
      cancelled = true;
    };
  }, [infoName]);

  const skills = useMemo(
    () => (payload?.skills || []).filter((skill) => skill.assigned || skill.origin !== "bundled"),
    [payload],
  );
  const groups = useMemo(() => groupSkills(skills), [skills]);
  const term = applied.trim().toLowerCase();
  const teams = payload?.skill_teams || [];

  const visibleCount = countVisible(groups, term, openRepos);
  const idle = idleStatus(groups, skills);
  const status = term
    ? visibleCount
      ? `Showing ${visibleCount} skill${visibleCount === 1 ? "" : "s"}`
      : "No skills match."
    : payload
      ? idle
      : "Loading skills…";

  async function saveEnabled(nextChecked: string[]) {
    const enabled = [...nextChecked];
    for (const name of payload?.assigned || []) {
      if (!enabled.includes(name)) enabled.push(name);
    }
    try {
      const body = await api<SkillsPayload>("/api/workspaces/skills", {
        method: "PUT",
        body: JSON.stringify({ path: workspacePath(), enabled }),
      });
      publishSkills(body);
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "failed to update skills", "Could not update skills");
    }
  }

  function checkedNames(except?: { name: string; enabled: boolean }): string[] {
    const names = skills
      .filter((skill) => skill.origin !== "bundled" && skill.enabled)
      .map((skill) => skill.name);
    if (!except) return names;
    return except.enabled
      ? [...names.filter((name) => name !== except.name), except.name]
      : names.filter((name) => name !== except.name);
  }

  async function cloneSource() {
    const next = url.trim();
    if (!next) return;
    try {
      const body = await api<SkillsPayload>("/api/workspaces/skills/sources", {
        method: "POST",
        body: JSON.stringify({
          path: workspacePath(),
          url: next,
          team_id: teams.length >= 2 ? teamId : "",
        }),
      });
      setUrl("");
      publishSkills(body);
      notify("success", "Stored the skill repository.", "Skills cloned");
    } catch (err) {
      notify("error", message(err, "failed to clone skills"), "Could not clone skills");
    }
  }

  async function syncSource(slug: string) {
    try {
      const body = await api<SkillsPayload>("/api/workspaces/skills/sources/sync", {
        method: "POST",
        body: JSON.stringify({ path: workspacePath(), slug }),
      });
      publishSkills(body);
      notify("success", "Pulled the latest commit into the skill space.", "Skills updated");
    } catch (err) {
      notify("error", message(err, "failed to pull skills"), "Could not pull skills");
    }
  }

  async function removeSource(slug: string) {
    try {
      const body = await api<SkillsPayload>("/api/workspaces/skills/sources/remove", {
        method: "POST",
        body: JSON.stringify({ path: workspacePath(), slug }),
      });
      publishSkills(body);
      notify("success", "Removed the skill repository from this project.", "Skills");
    } catch (err) {
      notify("error", message(err, "failed to remove the skill repository"), "Could not remove the skill repository");
    }
  }

  return (
    <>
      <div className="settings-skills-search">
        <input
          type="search"
          id="skills-search"
          aria-label="Search skills"
          placeholder="Search skills…"
          autoComplete="off"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="settings-skills-list" id="workspace-skills">
        {groups.loose
          .filter((skill) => skillMatches(skill, term))
          .map((skill) => (
            <SkillRow
              key={skill.name}
              skill={skill}
              expanded={infoName === skill.name}
              onToggle={(enabled) => {
                void saveEnabled(checkedNames({ name: skill.name, enabled }));
              }}
              onInfo={() => setInfoName(skill.name)}
            />
          ))}
        {groups.repos.map((group) => {
          const opened = Boolean(openRepos[group.url]);
          const repoMatches = Boolean(term) && group.label.toLowerCase().includes(term);
          const rows = group.skills.filter((skill) => repoMatches || skillMatches(skill, term));
          if (term && rows.length === 0) return null;
          const folded = !term && !opened;
          const toggleable = group.skills.filter((skill) => skill.origin !== "bundled");
          const onCount = toggleable.filter((skill) => skill.enabled).length;
          const mixed = onCount > 0 && onCount < toggleable.length;
          const repoOn = toggleable.length > 0 && onCount === toggleable.length;
          return (
            <section
              key={group.url}
              className={folded ? "org-skill-repo folded" : "org-skill-repo"}
              data-url={group.url}
              data-repo={group.label}
              data-open={opened ? "true" : "false"}
            >
              <div className="org-skill-head">
                <button
                  type="button"
                  className="skill-repo-fold"
                  data-skill-repo={group.url}
                  aria-expanded={folded ? "false" : "true"}
                  title={group.url}
                  onClick={() => {
                    if (term) return;
                    setOpenRepos((current) => ({ ...current, [group.url]: !current[group.url] }));
                  }}
                >
                  <span className="skill-repo-title">
                    <span className="skill-repo-mark" aria-hidden="true">
                      {folded ? "▸" : "▾"}
                    </span>
                    <span>{group.label}</span>
                  </span>
                  <span className="skill-repo-count">{String(group.skills.length)}</span>
                </button>
                {toggleable.length > 0 ? (
                  <input
                    type="checkbox"
                    className="org-switch"
                    aria-label={`Enable ${group.label}`}
                    checked={repoOn}
                    ref={(node) => {
                      if (node) node.indeterminate = mixed;
                    }}
                    onChange={(event) => {
                      const wanted = event.target.checked;
                      const next = new Set(checkedNames());
                      for (const skill of toggleable) {
                        if (wanted) next.add(skill.name);
                        else next.delete(skill.name);
                      }
                      void saveEnabled([...next]);
                    }}
                  />
                ) : null}
              </div>
              {folded ? null : (
                <div className="skill-repo-body">
                  {(term ? rows : group.skills).map((skill) => (
                    <SkillRow
                      key={skill.name}
                      skill={skill}
                      expanded={infoName === skill.name}
                      onToggle={(enabled) => {
                        void saveEnabled(checkedNames({ name: skill.name, enabled }));
                      }}
                      onInfo={() => setInfoName(skill.name)}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
      <p className="hint" id="skills-status">
        {status}
      </p>
      <div className="settings-skill-sources" id="skill-sources">
        {(payload?.sources || []).map((source) => {
          const slug = source.id || source.slug || "";
          const short = source.sha ? source.sha.slice(0, 7) : "";
          return (
            <div className="skill-source-row" key={slug || source.url}>
              <span title={source.sha || source.url}>
                {short ? `${source.url} · ${short}` : source.url}
              </span>
              {source.can_sync !== false ? (
                <button type="button" className="secondary skill-sync" data-slug={slug} onClick={() => void syncSource(slug)}>
                  Resync
                </button>
              ) : null}
              {source.scope_type === "project" && source.can_sync !== false ? (
                <button
                  type="button"
                  className="secondary"
                  data-skill-source-remove={slug}
                  onClick={() => void removeSource(slug)}
                >
                  Remove
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="settings-skill-source">
        <select
          id="skill-source-team"
          hidden={teams.length < 2}
          aria-label="Team"
          value={teamId || teams[0]?.id || ""}
          onChange={(event) => setTeamId(event.target.value)}
        >
          {teams.map((team) => (
            <option key={team.id} value={team.id}>
              {team.name}
            </option>
          ))}
        </select>
        <input
          id="skill-source-url"
          type="url"
          placeholder="https://github.com/org/skills.git"
          autoComplete="off"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        <button type="button" className="secondary" id="clone-skill-source" onClick={() => void cloneSource()}>
          Clone
        </button>
      </div>
      <div className="skill-info-pop" id="skill-info-pop" hidden={!infoName}>
        <div className="skill-info-pop-head">
          <h3 id="skill-info-title">{infoName || "Skill"}</h3>
          <button type="button" className="secondary" id="close-skill-info" onClick={() => setInfoName("")}>
            Close
          </button>
        </div>
        <div className="skill-info-pop-body" id="skill-info-body">
          {infoError ? <p className="hint">{infoError}</p> : null}
          {!info && !infoError ? <p className="hint">Loading skill…</p> : null}
          {info ? (
            <>
              <p className="hint">{originLabel(info)}</p>
              {info.description ? <p className="skill-info-lead">{info.description}</p> : null}
              {info.html ? (
                <article className="skill-markdown" dangerouslySetInnerHTML={{ __html: info.html }} />
              ) : info.body ? (
                <pre>{info.body}</pre>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}

function SkillRow(props: {
  skill: Skill;
  hidden?: boolean;
  expanded: boolean;
  onToggle: (enabled: boolean) => void;
  onInfo: () => void;
}) {
  const locked = props.skill.origin === "bundled";
  const id = `skill-enable-${props.skill.name}`;
  const tip = locked
    ? "Assigned from the organization."
    : props.skill.description || props.skill.summary || props.skill.name;
  return (
    <div className={props.hidden ? "skill-row hidden" : "skill-row"}>
      <input
        type="checkbox"
        className="org-switch"
        id={id}
        checked={locked || Boolean(props.skill.enabled)}
        disabled={locked}
        data-skill={props.skill.name}
        aria-label={`Enable ${props.skill.name}`}
        title={locked ? "Assigned from the organization." : undefined}
        onChange={(event) => props.onToggle(event.target.checked)}
      />
      <div className="skill-row-copy">
        <strong title={tip}>{props.skill.name}</strong>
      </div>
      <button
        type="button"
        className="skill-info"
        data-skill={props.skill.name}
        aria-label={`About ${props.skill.name}`}
        title={`About ${props.skill.name}`}
        aria-expanded={props.expanded ? "true" : "false"}
        aria-controls="skill-info-pop"
        onClick={(event) => {
          event.preventDefault();
          props.onInfo();
        }}
      >
        {INFO_ICON}
      </button>
    </div>
  );
}

function groupSkills(skills: Skill[]) {
  const loose: Skill[] = [];
  const grouped = new Map<string, Skill[]>();
  for (const skill of skills) {
    const url = String(skill.source_url || "").trim();
    if (!url) {
      loose.push(skill);
      continue;
    }
    const list = grouped.get(url) || [];
    list.push(skill);
    grouped.set(url, list);
  }
  const repos = [...grouped.keys()]
    .sort((left, right) => repoLabel(left, left).localeCompare(repoLabel(right, right)))
    .map((url) => ({
      url,
      label: repoLabel(url, url),
      skills: grouped.get(url) || [],
    }));
  return { loose, repos };
}

function skillMatches(skill: Skill, term: string): boolean {
  if (!term) return true;
  const text = `${skill.name} ${skill.summary || ""} ${skill.description || ""}`.toLowerCase();
  return text.includes(term);
}

function countVisible(
  groups: ReturnType<typeof groupSkills>,
  term: string,
  openRepos: Record<string, boolean>,
): number {
  let count = groups.loose.filter((skill) => skillMatches(skill, term)).length;
  for (const group of groups.repos) {
    const repoMatches = Boolean(term) && group.label.toLowerCase().includes(term);
    const opened = Boolean(openRepos[group.url]);
    for (const skill of group.skills) {
      const match = repoMatches || skillMatches(skill, term);
      if (match && (term || opened)) count += 1;
    }
  }
  return count;
}

function idleStatus(groups: ReturnType<typeof groupSkills>, skills: Skill[]): string {
  if (!skills.length) return "No skills are assigned to this project.";
  const bits = groups.loose.map((skill) => skill.name);
  for (const group of groups.repos) bits.push(`${group.label} (${group.skills.length})`);
  return bits.join(", ");
}

function originLabel(skill: Skill): string {
  if (skill.origin === "bundled") return "Bundled with LBW";
  if (skill.origin === "local") return "Local workspace skill";
  if (skill.origin === "repo") return "From repository";
  if (skill.origin === "source") return `From ${skill.source_url || ""}`;
  return "Cloned skill repo";
}

function message(err: unknown, fallback: string): string {
  if (err instanceof ApiError || err instanceof Error) return err.message || fallback;
  return fallback;
}

function publishSkills(body: SkillsPayload) {
  const page = window as SkillsWindow;
  page.__lbwProjectSkills = body;
  document.dispatchEvent(new CustomEvent("lbw-project-skills", { detail: body }));
  const hint = document.getElementById("enabled-skills-hint");
  if (hint && body.enabled) {
    hint.textContent = body.enabled.length ? `Skills: ${body.enabled.join(", ")}` : "";
  }
}

type SkillsWindow = Window & {
  __lbwProjectSkills?: SkillsPayload;
};
