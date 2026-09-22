import { App } from 'aws-cdk-lib/core';
import { Template } from 'aws-cdk-lib/assertions';
import { PublicSnapshotStack } from '../lib/public-snapshot-stack';

/**
 * Guards the one property that makes a public bucket safe here: the
 * `s3:GetObject` grant is scoped to the single `status.json` key, never to
 * the bucket itself or to `/*`. Synthesizes in-process via
 * `Template.fromStack` — no credentials, no network, no deploy.
 */
function synthesizeTemplate(): Template {
  const app = new App();
  const stack = new PublicSnapshotStack(app, 'TestStack');
  return Template.fromStack(stack);
}

/** Every IAM statement in the synthesized template, whatever resource type
 * carries it — bucket policies, managed policies, and the inline policies a
 * custom resource's role would bring with it. Written as a walk rather than
 * a list of known resource types so a statement added by a construct nobody
 * remembered is still seen. */
function everyStatement(template: Template): Array<Record<string, unknown>> {
  const statements: Array<Record<string, unknown>> = [];
  const visit = (node: unknown): void => {
    if (Array.isArray(node)) {
      node.forEach(visit);
      return;
    }
    if (node === null || typeof node !== 'object') return;
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      if (key === 'Statement' && Array.isArray(value)) {
        statements.push(...(value as Array<Record<string, unknown>>));
      }
      visit(value);
    }
  };
  visit(template.toJSON());
  return statements;
}

function actionsOf(statement: Record<string, unknown>): string[] {
  const action = statement.Action;
  if (typeof action === 'string') return [action];
  if (Array.isArray(action)) return action.filter((entry): entry is string => typeof entry === 'string');
  return [];
}

describe('PublicSnapshotStack bucket policy', () => {
  test('the only public statement grants s3:GetObject on the bucket ARN joined with /status.json, not on the bucket or /*', () => {
    const template = synthesizeTemplate();

    const buckets = template.findResources('AWS::S3::Bucket');
    const bucketLogicalIds = Object.keys(buckets);
    expect(bucketLogicalIds).toHaveLength(1);
    const [bucketLogicalId] = bucketLogicalIds;

    const policies = template.findResources('AWS::S3::BucketPolicy');
    const policyLogicalIds = Object.keys(policies);
    expect(policyLogicalIds).toHaveLength(1);

    const statements = policies[policyLogicalIds[0]].Properties.PolicyDocument
      .Statement as Array<Record<string, unknown>>;

    // Filter down to the statement whose principal is the public ("*"),
    // which is the entire security argument for this bucket.
    const publicStatements = statements.filter(
      (statement) => JSON.stringify(statement.Principal) === JSON.stringify({ AWS: '*' }),
    );
    expect(publicStatements).toHaveLength(1);
    const [publicStatement] = publicStatements;

    expect(publicStatement.Action).toBe('s3:GetObject');

    // Written to fail if the resource were the bucket ARN alone (no Join)
    // or ended in "/*" instead of "/status.json".
    expect(publicStatement.Resource).toEqual({
      'Fn::Join': ['', [{ 'Fn::GetAtt': [bucketLogicalId, 'Arn'] }, '/status.json']],
    });
  });

  test('PublicAccessBlockConfiguration blocks ACLs but allows this bucket policy to grant public read', () => {
    const template = synthesizeTemplate();

    template.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        IgnorePublicAcls: true,
        BlockPublicPolicy: false,
        RestrictPublicBuckets: false,
      },
    });
  });

  test('nothing in this stack may rewrite the bucket policy', () => {
    // `blockPublicPolicy: false` is required for anonymous read, which means
    // S3 will accept whatever policy a holder of s3:PutBucketPolicy writes.
    // A principal with that permission can grant s3:PutObject to "*" and
    // decide what every resident reads. `autoDeleteObjects: true` granted
    // exactly that to a Lambda's role, to save one manual step on a
    // `cdk destroy` of a bucket holding one object.
    const withPutBucketPolicy = everyStatement(synthesizeTemplate()).filter((statement) =>
      actionsOf(statement).some((action) => action === 's3:PutBucketPolicy' || action === 's3:*'),
    );

    expect(withPutBucketPolicy).toEqual([]);
  });

  test('the bucket keeps previous versions of status.json', () => {
    // One object, read by every resident, with no history: if the cycle's
    // credential leaks, a well-formed document reading "sin riesgo" during a
    // real flood renders faithfully and there is nothing to compare it to.
    const template = synthesizeTemplate();

    template.hasResourceProperties('AWS::S3::Bucket', {
      VersioningConfiguration: { Status: 'Enabled' },
    });
  });
});

describe('PublicSnapshotStack writer policy', () => {
  /** The stack said who may read and nothing about who may write, so the
   * write grant would be attached by hand later — in practice as something
   * broad. */
  function writerStatements(template: Template): Array<Record<string, unknown>> {
    const policies = template.findResources('AWS::IAM::ManagedPolicy');
    const logicalIds = Object.keys(policies);
    expect(logicalIds).toHaveLength(1);
    return policies[logicalIds[0]].Properties.PolicyDocument.Statement as Array<Record<string, unknown>>;
  }

  test('the writer may put the one object and do nothing else', () => {
    const template = synthesizeTemplate();
    const [bucketLogicalId] = Object.keys(template.findResources('AWS::S3::Bucket'));

    const statements = writerStatements(template);

    expect(statements).toHaveLength(1);
    expect(statements[0].Action).toBe('s3:PutObject');
    expect(statements[0].Effect).toBe('Allow');
    // No DeleteObject, no other key, not the bucket, not "/*".
    expect(statements[0].Resource).toEqual({
      'Fn::Join': ['', [{ 'Fn::GetAtt': [bucketLogicalId, 'Arn'] }, '/status.json']],
    });
  });

  test('its ARN is an output, so the runbook names a policy instead of asking an operator to invent one', () => {
    const outputs = synthesizeTemplate().findOutputs('*');

    expect(Object.keys(outputs)).toContain('SnapshotWriterPolicyArn');
  });
});
