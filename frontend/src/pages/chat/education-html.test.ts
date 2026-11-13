import { describe, expect, test } from "bun:test"
import { educationHtmlJobID } from "./education-html"

describe("educationHtmlJobID", () => {
  test("identifies canonical education preview HTML", () => {
    expect(educationHtmlJobID("education-previews/job_123.html")).toBe("job_123")
  })

  test("does not grant education actions to other HTML paths", () => {
    expect(educationHtmlJobID("article.html")).toBeUndefined()
    expect(educationHtmlJobID("education-previews/nested/job_123.html")).toBeUndefined()
    expect(educationHtmlJobID("education-previews/job_123.htm")).toBeUndefined()
    expect(educationHtmlJobID("education-previews/job_123.HTML")).toBeUndefined()
  })
})
