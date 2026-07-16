import { Route } from "@solidjs/router"
import { ChatLayout } from "./layout"

export const chatRoutes = (
  <>
    <Route path="/chat" component={ChatLayout} />
    <Route path="/chat/:id" component={ChatLayout} />
  </>
)