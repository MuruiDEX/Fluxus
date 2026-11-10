import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  Button,
  Checkbox,
  IconButton,
  Input,
  Select,
  Textarea,
  buttonClass,
} from "@/components/system/controls";
import {
  FieldError,
  FieldGroup,
  FieldHelp,
  FieldLabel,
  FormField,
  type FieldControlAria,
} from "@/components/system/form-field";
import { cn } from "@/lib/cn";

/**
 * These render through `react-dom/server`, which exercises the same markup
 * Server Components produce — no DOM dependency was added to the project.
 */
const html = (node: React.ReactNode) => renderToStaticMarkup(<>{node}</>);

describe("Button", () => {
  it("uses the solid brand action token for the primary identity", () => {
    const markup = html(<Button>Launch</Button>);
    expect(markup).toContain("bg-brand-solid");
    expect(markup).toContain("text-on-brand");
    expect(markup).toContain("hover:bg-brand-solid-hover");
    expect(markup).not.toContain("from-brand-500");
    expect(markup).not.toContain("to-sky-400");
    // Brand text/accent is not an action fill substitute.
    expect(markup).not.toContain("bg-brand ");
    expect(markup).toContain("Launch");
  });

  it("keeps each neutral variant visually distinct", () => {
    const secondary = html(<Button variant="secondary">Cancel</Button>);
    expect(secondary).toContain("border-line");
    expect(secondary).toContain("bg-surface");
    expect(secondary).toContain("text-fg");

    const outline = html(<Button variant="outline">Cancel</Button>);
    expect(outline).toContain("border-line");
    expect(outline).toContain("bg-transparent");
    expect(outline).not.toContain("bg-surface ");

    const ghost = html(<Button variant="ghost">Cancel</Button>);
    expect(ghost).toContain("bg-transparent");
    expect(ghost).toContain("text-fg-muted");
    expect(ghost).toContain("border-transparent");

    const destructive = html(<Button variant="destructive">Delete</Button>);
    expect(destructive).toContain("bg-danger-soft");
    expect(destructive).toContain("text-danger-fg");
    expect(destructive).toContain("border-danger-line");
    expect(destructive).not.toContain("from-brand-500");
  });

  it("exposes small, medium and large size contracts", () => {
    expect(html(<Button size="sm">Small</Button>)).toContain("h-8");
    expect(html(<Button size="md">Medium</Button>)).toContain("h-9");
    expect(html(<Button size="lg">Large</Button>)).toContain("h-11");
  });

  it("defaults to a non-submitting button type and accepts submit", () => {
    expect(html(<Button>Safe</Button>)).toContain('type="button"');
    expect(html(<Button type="submit">Save</Button>)).toContain('type="submit"');
  });

  it("reflects disabled state natively through semantic disabled tokens", () => {
    const markup = html(<Button disabled>Busy</Button>);
    expect(markup).toContain('disabled=""');
    expect(markup).toContain("disabled:cursor-not-allowed");
    expect(markup).toContain("disabled:bg-disabled-bg");
    expect(markup).toContain("disabled:text-disabled-fg");
    expect(markup).toContain("disabled:border-disabled-line");
  });

  it("renders a keyboard-visible focus ring on the semantic focus token", () => {
    const markup = html(<Button>Focus me</Button>);
    expect(markup).toContain("focus-visible:outline-2");
    expect(markup).toContain("focus-visible:outline-focus");
  });

  it("marks loading, disables the control and keeps its label in the layout", () => {
    const markup = html(
      <Button loading type="submit">
        Saving
      </Button>,
    );
    expect(markup).toContain('aria-busy="true"');
    expect(markup).toContain('disabled=""');
    // The label stays rendered so the button keeps its width; only visibility changes.
    expect(markup).toContain("Saving");
    expect(markup).toContain("invisible");
    // Spinner is decorative and absolutely positioned (no reflow).
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain("animate-spin");
    expect(markup).toContain("absolute");
  });

  it("does not set aria-busy when idle", () => {
    expect(html(<Button>Idle</Button>)).not.toContain("aria-busy");
  });

  it("gives icon-only buttons an accessible name and a square size", () => {
    const markup = html(
      <IconButton label="Close dialog" variant="ghost">
        <svg aria-hidden />
      </IconButton>,
    );
    expect(markup).toContain('aria-label="Close dialog"');
    expect(markup).toContain("w-9");
  });

  it("allows caller classes to override sizing through cn()", () => {
    const markup = html(<Button className="h-8">Small</Button>);
    expect(markup).toContain("h-8");
    expect(markup).not.toContain("h-9");
  });

  it("merges variant and caller classes without dropping semantic type sizes", () => {
    const merged = buttonClass({ variant: "ghost", className: "h-8 text-caption" });
    expect(merged).toContain("h-8");
    expect(merged).not.toContain("h-9");
    expect(merged).toContain("text-caption");
    expect(merged).not.toContain("text-body-sm");
  });
});

describe("form controls", () => {
  it("renders the shared input chrome and forwards native attributes", () => {
    const markup = html(
      <Input
        id="email"
        name="email"
        type="email"
        autoComplete="username"
        placeholder="you@company.com"
        aria-describedby="email-description"
      />,
    );
    expect(markup).toContain('type="email"');
    expect(markup).toContain('autoComplete="username"');
    expect(markup).toContain('placeholder="you@company.com"');
    expect(markup).toContain('aria-describedby="email-description"');
    expect(markup).toContain("rounded-control");
    expect(markup).toContain("h-10");
    expect(markup).toContain("rounded-control");
    expect(markup).toContain("placeholder:text-fg-subtle");
    expect(markup).toContain("focus:border-focus");
  });

  it("exposes disabled, read-only and invalid input states", () => {
    const disabled = html(<Input id="locked" disabled aria-invalid />);
    expect(disabled).toContain('disabled=""');
    expect(disabled).toContain('aria-invalid="true"');
    expect(disabled).toContain("disabled:cursor-not-allowed");
    expect(disabled).toContain("disabled:bg-disabled-bg");
    expect(disabled).toContain("aria-invalid:border-danger");
    expect(disabled).toContain("aria-invalid:ring-danger/30");

    const readOnly = html(<Input id="code" readOnly value="ABC-123" />);
    expect(readOnly).toContain('readOnly=""');
    expect(readOnly).toContain("read-only:bg-surface-sunken");
    expect(readOnly).toContain("read-only:text-fg-muted");
  });

  it("lets callers override input height without losing other styles", () => {
    const markup = html(<Input id="compact" className="h-8" />);
    expect(markup).toContain("h-8");
    expect(markup).not.toContain("h-10");
    expect(markup).toContain("rounded-control");
  });

  it("renders a textarea with native rows and value props", () => {
    const markup = html(
      <Textarea id="goal" rows={3} defaultValue="Grow the studio" aria-invalid />,
    );
    expect(markup).toContain("<textarea");
    expect(markup).toContain('rows="3"');
    expect(markup).toContain("Grow the studio");
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain("min-h-20");
  });

  it("renders native select options and disabled state", () => {
    const markup = html(
      <Select id="status" defaultValue="active" disabled>
        <option value="planned">Planned</option>
        <option value="active">Active</option>
      </Select>,
    );
    expect(markup).toContain("<select");
    expect(markup).toContain("<option");
    expect(markup).toContain("Planned");
    expect(markup).toContain('disabled=""');
    expect(markup).toContain("rounded-control");
    expect(markup).toContain("pr-9");
    expect(markup).toContain("focus:border-focus");
  });

  it("renders a native checkbox that keeps checked and disabled semantics", () => {
    const markup = html(
      <Checkbox id="team-1" name="team" value="agent-1" defaultChecked disabled />,
    );
    expect(markup).toContain('type="checkbox"');
    expect(markup).toContain('checked=""');
    expect(markup).toContain('disabled=""');
    expect(markup).toContain("accent-brand-solid");
    expect(markup).toContain("focus-visible:outline-focus");
    expect(markup).toContain("aria-invalid:outline-danger");
  });
});

describe("FormField", () => {
  const renderEmail = () =>
    html(
      <FormField
        id="email"
        label="Email"
        description="Use your work address."
        error="Email is required."
        required
      >
        {(control: FieldControlAria) => (
          <Input id="email" type="email" required {...control} />
        )}
      </FormField>,
    );

  it("associates the label with the control by id", () => {
    const markup = renderEmail();
    expect(markup).toContain('for="email"');
    expect(markup).toContain('id="email"');
  });

  it("links description and error through aria-describedby", () => {
    const markup = renderEmail();
    expect(markup).toContain('id="email-description"');
    expect(markup).toContain('id="email-error"');
    expect(markup).toContain('aria-describedby="email-description email-error"');
  });

  it("marks the control invalid and announces the error", () => {
    const markup = renderEmail();
    expect(markup).toContain('aria-invalid="true"');
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("Email is required.");
  });

  it("renders the required indicator accessibly", () => {
    const markup = renderEmail();
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain("sr-only");
    expect(markup).toContain("(required)");
  });

  it("omits optional wiring when there is no description, error or required flag", () => {
    const markup = html(
      <FormField id="nickname" label="Nickname">
        {(control) => <Input id="nickname" {...control} />}
      </FormField>,
    );
    expect(markup).not.toContain('aria-describedby="');
    expect(markup).not.toContain('aria-invalid="true"');
    expect(markup).not.toContain("(required)");
  });
});

describe("shared field primitives", () => {
  it("keeps label, help and error styling in one place", () => {
    const markup = html(
      <>
        <FieldLabel htmlFor="project" required>
          Project
        </FieldLabel>
        <FieldHelp id="project-help">Choose an existing project.</FieldHelp>
        <FieldError id="project-error">Project is required.</FieldError>
      </>,
    );
    expect(markup).toContain('for="project"');
    expect(markup).toContain("(required)");
    expect(markup).toContain('id="project-help"');
    expect(markup).toContain('id="project-error"');
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("text-caption");
    expect(markup).toContain("text-label");
    expect(markup).toContain("text-danger");
  });
});

describe("FieldGroup", () => {
  it("groups checkboxes with a legend and fieldset-level descriptions", () => {
    const markup = html(
      <FieldGroup id="team" legend="Team" description="Pick everyone who works on this." required>
        <label htmlFor="team-1">
          <Checkbox id="team-1" /> Scout
        </label>
      </FieldGroup>,
    );
    expect(markup).toContain("<fieldset");
    expect(markup).toContain("<legend");
    expect(markup).toContain('aria-describedby="team-description"');
    expect(markup).toContain('id="team-description"');
    expect(markup).toContain("(required)");
  });
});

describe("control primitives stay Server-Component friendly", () => {
  const read = (file: string) => readFileSync(path.join(process.cwd(), file), "utf8");

  it("does not declare a client boundary or import hooks", () => {
    for (const file of ["components/system/controls.tsx", "components/system/form-field.tsx"]) {
      const source = read(file);
      expect(source).not.toContain('"use client"');
      expect(source).not.toContain("useId");
      expect(source).not.toContain("cloneElement(");
    }
  });

  it("keeps existing button wrappers delegating to the shared primitive", () => {
    const ui = read("components/system/ui.tsx");
    expect(ui).toContain("<Button variant=\"primary\"");
    // GhostButton keeps its historical bordered-surface look through the
    // canonical `secondary` variant.
    expect(ui).toContain("<Button variant=\"secondary\"");
    expect(ui).toContain("buttonClass({ variant: \"primary\" })");
    expect(ui).toContain("buttonClass({ variant: \"secondary\" })");
  });

  it("keeps modal form chrome as aliases of the canonical controls", () => {
    const writeModal = read("components/system/WriteModal.tsx");
    expect(writeModal).toContain("export const inputClass = canonicalInputClass;");
    expect(writeModal).toContain("export const areaClass = canonicalTextareaClass;");
  });
});

describe("canonical control token usage", () => {
  const read = (file: string) => readFileSync(path.join(process.cwd(), file), "utf8");

  it("does not fall back to raw palette, arbitrary sizes or gradients", () => {
    const source = read("components/system/controls.tsx");
    expect(source).not.toMatch(/gradient/);
    expect(source).not.toMatch(/\b(?:brand-500|brand-600|mist-\d+|ink-\d+|slate-\d+)\b/);
    expect(source).not.toMatch(/rounded-\[|text-\[\d/);
  });
});

describe("cn() merging for controls", () => {
  it("resolves conflicting spacing and keeps semantic type tokens", () => {
    const merged = cn("h-10 px-3 text-body-sm", "h-8");
    expect(merged).toContain("h-8");
    expect(merged).toContain("px-3");
    expect(merged).toContain("text-body-sm");
    expect(merged).not.toContain("h-10");
    expect(cn("text-body-sm", "text-fg")).toBe("text-body-sm text-fg");
    expect(cn("border border-line", "border-danger")).toBe("border border-danger");
  });
});
