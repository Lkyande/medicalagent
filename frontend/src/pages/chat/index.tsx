import { Route } from "@solidjs/router"
import { ChatLayout } from "./layout"

export const chatRoutes = (
  <Route path={["/chat", "/chat/:id", "/clinical", "/clinical/:id", "/disease-management", "/disease-management/:id", "/education", "/education/:id", "/door-window", "/door-window/:id"]} component={ChatLayout} />
)
