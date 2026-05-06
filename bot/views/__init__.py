"""Discord UI components: buttons, modals, selects, and pagination views.

Views are kept separate from the cogs that use them because:
* the on_ready handler needs to register persistent views *before* a user
  ever interacts with one — that registration code can import from here
  without pulling in the entire command stack;
* keeping a view in its own file makes it easier to evolve its layout
  without scrolling past hundreds of lines of command code.
"""
