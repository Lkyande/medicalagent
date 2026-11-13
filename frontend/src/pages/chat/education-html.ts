const EDUCATION_HTML_PATH = /^education-previews\/([A-Za-z0-9_-]{1,80})\.html$/

export function educationHtmlJobID(path: string): string | undefined {
  return EDUCATION_HTML_PATH.exec(path)?.[1]
}
