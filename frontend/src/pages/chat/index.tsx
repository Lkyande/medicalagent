import { Route } from "@solidjs/router"
import { ChatLayout } from "./layout"

export const chatRoutes = (
  <Route path={["/chat", "/chat/:id", "/clinical", "/clinical/:id"]} component={ChatLayout} />
)
