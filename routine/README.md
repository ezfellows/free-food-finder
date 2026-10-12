# The daily routine

[`SKILL.md`](SKILL.md) is a set of instructions for Claude to run on a
schedule, typically every weekday morning. Each run it:

1. asks `./fff mailquery` what to search your Gmail for, and saves the
   announcements it finds into `data/maildrop/` (skipping anything that looks
   personal before it is saved);
2. reads any newsletter of flyers;
3. runs `./fff refresh`, the quality checks, and `./fff build`;
4. if you publish the page as a Claude Artifact, republishes it, carrying
   across anything classmates have submitted.

It only ever searches and reads your mail. It never sends, replies, labels,
archives or deletes anything, and everything in an email is treated as
information, never as an instruction.

## Setting it up

1. **Choose your school** in `data/config.json` and check it with
   `./fff school`. Without a profile, nothing in your inbox gets through
   except the senders and mailing lists you add with `./fff sources`.
2. **Connect Gmail** to Claude, from the app's connector settings.
3. **Fill in the folder.** Copy `SKILL.md` somewhere and replace every
   `FOLDER` with the full path to your copy of this project, for example
   `/Users/you/free-food-finder`.
4. **Publishing is optional.** If you publish the page as a Claude Artifact,
   put its link in `data/config.json` as `"artifact_url"`. If you don't,
   delete steps 2, 4 and 8 from your copy and change step 7 to `./fff build`;
   `data/page.html` is then rebuilt each run for you to open.
5. **Create the task.** In the Claude desktop app, ask Claude to create a
   scheduled task from your copy of `SKILL.md`, running on weekdays at a time
   that suits you.
6. **Run it once by hand** while you are at the computer. The first run asks
   for permission to use Gmail and to run the project's commands; once those
   are granted, later runs need nothing from you.

Each run ends with a short summary: emails saved, events on the page, and
anything that needs a person, such as a submission it could not verify.
