"""Project-role scenarios: per-membership owner/member roles and the
owner-gated project clear. These lock in that:
- the first member of a project owns it; later joiners are members
- only an owner (or a global owner) can clear a project's memory
- clear is scoped to a single project and wipes nothing else
- a non-member cannot clear
- re-joining a project preserves an owner's role (no positional reassignment)
"""


async def test_first_member_owns_others_are_members(scenario):
    founder = await scenario.agent("founder")
    joiner = await scenario.agent("joiner")
    await founder.join_project("expedition")
    await joiner.join_project("expedition")

    r = await founder.project_members("expedition")
    assert r.status_code == 200
    roles = {m["agent_id"]: m["role"] for m in r.json()}
    assert roles["founder"] == "owner"
    assert roles["joiner"] == "member"


async def test_only_owner_can_clear(scenario):
    owner = await scenario.agent("camp-owner")
    member = await scenario.agent("camp-member")
    await owner.join_project("camp")
    await member.join_project("camp")
    e1 = await owner.write_memory("owner intel", project="camp")
    e2 = await member.write_memory("member intel", project="camp")

    # a member cannot clear
    assert (await member.clear_project("camp")).status_code == 403
    # the owner can, and it wipes the whole project's memory
    assert (await owner.clear_project("camp")).status_code == 204
    assert (await owner._http.get(f"/memory/{e1['id']}")).status_code == 404
    assert (await owner._http.get(f"/memory/{e2['id']}")).status_code == 404


async def test_clear_is_scoped_to_one_project(scenario):
    a = await scenario.agent("alpha-owner")
    b = await scenario.agent("beta-owner")
    await a.join_project("alpha")
    await b.join_project("beta")
    ea = await a.write_memory("alpha map", project="alpha")
    eb = await b.write_memory("beta map", project="beta")

    assert (await a.clear_project("alpha")).status_code == 204
    assert (await a._http.get(f"/memory/{ea['id']}")).status_code == 404
    # beta is untouched
    assert (await b._http.get(f"/memory/{eb['id']}")).status_code == 200


async def test_global_owner_can_clear_any_project(scenario):
    agent = await scenario.agent("settler")
    await agent.join_project("frontier")
    e = await agent.write_memory("frontier intel", project="frontier")

    glob = await scenario.owner_agent()  # global owner, not a member of frontier
    assert (await glob.clear_project("frontier")).status_code == 204
    assert (await agent._http.get(f"/memory/{e['id']}")).status_code == 404


async def test_non_member_cannot_clear(scenario):
    owner = await scenario.agent("holder")
    outsider = await scenario.agent("outsider")
    await owner.join_project("vault")
    assert (await outsider.clear_project("vault")).status_code == 403


async def test_rejoin_preserves_owner_role(scenario):
    owner = await scenario.agent("chief")
    member = await scenario.agent("brave")
    await owner.join_project("tribe")
    await member.join_project("tribe")
    # the chief re-joins the same project; ownership must persist, not flip to member
    await owner.join_project("tribe")

    r = await owner.project_members("tribe")
    roles = {m["agent_id"]: m["role"] for m in r.json()}
    assert roles["chief"] == "owner"
    assert roles["brave"] == "member"
    # and the chief can still clear
    assert (await owner.clear_project("tribe")).status_code == 204
