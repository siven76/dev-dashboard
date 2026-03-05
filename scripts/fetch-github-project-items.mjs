#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";

const org = process.argv[2] ?? "mana-soft";
const projectNumber = Number(process.argv[3] ?? 1);
const outputFile = process.argv[4] ?? path.resolve(process.cwd(), `project-${org}-${projectNumber}-items.csv`);

if (!Number.isInteger(projectNumber) || projectNumber <= 0) {
  console.error("Project number must be a positive integer.");
  process.exit(1);
}

const query = `
query($org: String!, $projectNumber: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $projectNumber) {
      id
      title
      items(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          content {
            __typename
            ... on Issue {
              number
              title
              url
              state
              closedAt
              repository {
                nameWithOwner
              }
            }
            ... on PullRequest {
              number
              title
              url
              state
              closedAt
              repository {
                nameWithOwner
              }
            }
            ... on DraftIssue {
              title
            }
          }
          fieldValues(first: 50) {
            nodes {
              __typename
              ... on ProjectV2ItemFieldIterationValue {
                title
                startDate
                duration
                createdAt
                updatedAt
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldDateValue {
                date
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldNumberValue {
                number
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
              ... on ProjectV2ItemFieldTextValue {
                text
                field {
                  ... on ProjectV2FieldCommon {
                    name
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
`;

function runGh(args) {
  return execFileSync("gh", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

function runGraphQL(cursor) {
  const args = [
    "api",
    "graphql",
    "-f",
    `query=${query}`,
    "-F",
    `org=${org}`,
    "-F",
    `projectNumber=${projectNumber}`
  ];

  if (cursor) {
    args.push("-F", `cursor=${cursor}`);
  }

  try {
    return JSON.parse(runGh(args));
  } catch (error) {
    const stdout = error?.stdout?.toString?.() ?? "";
    const stderr = error?.stderr?.toString?.() ?? "";
    const combined = `${stdout}\n${stderr}`;
    if (combined.includes("INSUFFICIENT_SCOPES") || combined.includes("read:project")) {
      throw new Error(
        "Missing GitHub scope 'read:project'. Run: gh auth refresh -h github.com -s read:project"
      );
    }
    throw error;
  }
}

function checkAuth() {
  if (process.env.GH_TOKEN || process.env.GITHUB_TOKEN) {
    return;
  }

  try {
    const status = runGh(["auth", "status"]);
    const scopesLine = status
      .split("\n")
      .find((line) => line.toLowerCase().includes("token scopes:"));

    if (scopesLine && !scopesLine.includes("read:project")) {
      console.error("GitHub token is valid but missing 'read:project' scope.");
      console.error("Run: gh auth refresh -h github.com -s read:project");
      process.exit(1);
    }
  } catch (error) {
    const stderr = error?.stderr?.toString?.() ?? "";
    console.error("GitHub auth is not valid. Run: gh auth login -h github.com");
    if (stderr) {
      console.error(stderr.trim());
    }
    process.exit(1);
  }
}

function detectCompletionDate(dateFields) {
  const completionPattern = /(complete|completion|done|termine|ferme|closed)/i;
  const byName = dateFields.find((field) => completionPattern.test(field.fieldName));
  return byName ?? dateFields[0] ?? { fieldName: "", date: "" };
}

function detectPointValue(numberFields) {
  const pointsPattern = /(point|effort|story|estimate|estimation|charge|niveau effort)/i;
  const byName = numberFields.find((field) => pointsPattern.test(field.fieldName));
  return byName ?? numberFields[0] ?? { fieldName: "", value: "" };
}

function extractItems(projectData) {
  const rows = [];
  const projectTitle = projectData.title ?? "";
  const items = projectData.items?.nodes ?? [];

  for (const item of items) {
    const content = item.content;
    const contentType = content?.__typename ?? "Unknown";
    if (contentType !== "Issue" && contentType !== "PullRequest") {
      continue;
    }

    const fieldValues = item.fieldValues?.nodes ?? [];
    const iteration = fieldValues.find((node) => node.__typename === "ProjectV2ItemFieldIterationValue");

    const statusNode = fieldValues.find(
      (node) =>
        node.__typename === "ProjectV2ItemFieldSingleSelectValue" &&
        (node.field?.name ?? "").toLowerCase() === "status"
    );

    const dateFields = fieldValues
      .filter((node) => node.__typename === "ProjectV2ItemFieldDateValue")
      .map((node) => ({
        fieldName: node.field?.name ?? "",
        date: node.date ?? ""
      }));

    const completion = detectCompletionDate(dateFields);
    const numberFields = fieldValues
      .filter((node) => node.__typename === "ProjectV2ItemFieldNumberValue")
      .map((node) => ({
        fieldName: node.field?.name ?? "",
        value: node.number ?? ""
      }));
    const points = detectPointValue(numberFields);

    rows.push({
      projectTitle,
      itemType: contentType,
      repository: content.repository?.nameWithOwner ?? "",
      number: content.number ?? "",
      title: content.title ?? "",
      url: content.url ?? "",
      state: content.state ?? "",
      status: statusNode?.name ?? "",
      iteration: iteration?.title ?? "",
      iterationStartDate: iteration?.startDate ?? "",
      iterationDuration: iteration?.duration ?? "",
      iterationAddedAt: iteration?.createdAt ?? "",
      iterationUpdatedAt: iteration?.updatedAt ?? "",
      issueClosedAt: content.closedAt ?? "",
      completionDateField: completion.fieldName ?? "",
      completionDate: completion.date ?? "",
      pointsField: points.fieldName ?? "",
      pointsValue: points.value ?? ""
    });
  }

  return rows;
}

function toCsv(rows) {
  const headers = [
    "project_title",
    "item_type",
    "repository",
    "number",
    "title",
    "url",
    "state",
    "status",
    "iteration",
    "iteration_start_date",
    "iteration_duration",
    "iteration_added_at",
    "iteration_updated_at",
    "issue_closed_at",
    "completion_date_field",
    "completion_date",
    "points_field",
    "points_value"
  ];

  const escape = (value) => {
    const text = String(value ?? "");
    if (text.includes(",") || text.includes("\"") || text.includes("\n")) {
      return `"${text.replaceAll("\"", "\"\"")}"`;
    }
    return text;
  };

  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(
      [
        row.projectTitle,
        row.itemType,
        row.repository,
        row.number,
        row.title,
        row.url,
        row.state,
        row.status,
        row.iteration,
        row.iterationStartDate,
        row.iterationDuration,
        row.iterationAddedAt,
        row.iterationUpdatedAt,
        row.issueClosedAt,
        row.completionDateField,
        row.completionDate,
        row.pointsField,
        row.pointsValue
      ]
        .map(escape)
        .join(",")
    );
  }

  return lines.join("\n");
}

function main() {
  checkAuth();

  let cursor = "";
  let hasNextPage = true;
  const allRows = [];

  while (hasNextPage) {
    const response = runGraphQL(cursor);
    const project = response?.data?.organization?.projectV2;
    if (!project) {
      throw new Error(`Project #${projectNumber} not found in org "${org}" or access denied.`);
    }

    allRows.push(...extractItems(project));
    hasNextPage = Boolean(project.items?.pageInfo?.hasNextPage);
    cursor = project.items?.pageInfo?.endCursor ?? "";
  }

  const csv = toCsv(allRows);
  writeFileSync(outputFile, csv, "utf8");

  console.log(`Exported ${allRows.length} project items to ${outputFile}`);
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
