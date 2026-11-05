import { useEffect, useState } from "react";

import { api } from "../../api";
import { notify, workspacePath } from "../../page";

type Role = {
  name: string;
  title?: string;
  job?: string;
  summary?: string;
  description?: string;
  enabled?: boolean;
  required?: boolean;
  enforced?: boolean;
};

type RolesPayload = {
  roles?: Role[];
};

export function RolesSection() {
  const [roles, setRoles] = useState<Role[] | null>(null);

  useEffect(() => {
    function onRoles(event: Event) {
      const detail = (event as CustomEvent<RolesPayload>).detail || {};
      setRoles(detail.roles || []);
      paintStatus(detail.roles || []);
    }
    const cached = (window as RolesWindow).__lbwProjectRoles;
    if (cached) onRoles(new CustomEvent("lbw-project-roles", { detail: cached }));
    document.addEventListener("lbw-project-roles", onRoles);
    return () => document.removeEventListener("lbw-project-roles", onRoles);
  }, []);

  async function save(next: Role[]) {
    const enabled = next.filter((role) => role.enabled || role.enforced).map((role) => role.name);
    try {
      const body = await api<RolesPayload>("/api/workspaces/roles", {
        method: "PUT",
        body: JSON.stringify({ path: workspacePath(), enabled }),
      });
      publishRoles(body);
    } catch (err) {
      notify("error", err instanceof Error ? err.message : "failed to update roles", "Could not update roles");
    }
  }

  const listed = roles || [];
  return (
    <>
      {listed.map((role) => {
        const id = `role-enable-${role.name}`;
        const locked = Boolean(role.required || role.enforced);
        const tip = role.enforced
          ? "Enforced by the organization."
          : role.required
            ? "Builder and QA stay on."
            : role.summary || role.description || role.name;
        return (
          <div className="skill-row" key={role.name}>
            <input
              type="checkbox"
              className="org-switch"
              id={id}
              checked={Boolean(role.enabled || role.enforced)}
              disabled={locked}
              data-role={role.name}
              aria-label={`Enable ${role.title || role.name}`}
              title={locked ? tip : undefined}
              onChange={(event) => {
                const enabled = event.target.checked;
                void save(listed.map((item) => (item.name === role.name ? { ...item, enabled } : item)));
              }}
            />
            <div className="skill-row-copy">
              <strong title={tip}>{role.title || role.name}</strong>
            </div>
          </div>
        );
      })}
    </>
  );
}

function paintStatus(roles: Role[]) {
  const status = document.getElementById("roles-status");
  if (!status) return;
  const on = roles.filter((role) => role.enabled).map((role) => role.name);
  status.textContent = on.length ? `Enabled: ${on.join(", ")}` : "No roles enabled for this workspace.";
}

function publishRoles(body: RolesPayload) {
  const page = window as RolesWindow;
  page.__lbwProjectRoles = body;
  document.dispatchEvent(new CustomEvent("lbw-project-roles", { detail: body }));
}

type RolesWindow = Window & {
  __lbwProjectRoles?: RolesPayload;
};
